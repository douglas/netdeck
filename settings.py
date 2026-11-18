import ipaddress
import json
import subprocess
import sys
import urllib.error
import urllib.request

class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, f'Unexpected redirect {code}', headers, fp)
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

def nm(*args):
    p = subprocess.run(['nmcli', '--escape', 'no', *args], capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise ValueError(p.stderr.strip() or 'NetworkManager request failed')
    return p.stdout.strip()

def read_profile(uuid):
    values = nm('-g', 'connection.id,connection.type,ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns', 'connection', 'show', 'uuid', uuid).split('\n')
    values += [''] * (6-len(values))
    return dict(zip(['name', 'type', 'method', 'addresses', 'gateway', 'dns'], values), uuid=uuid)

def keyed(*args):
    """Read `nmcli -t -f` output as a dict. Keys keep no `[n]` suffix; a key
    that repeats (IP4.ADDRESS[1], [2]) becomes a comma-separated value."""
    out = {}
    for line in nm('-t', '-f', *args).splitlines():
        key, _, value = line.partition(':')
        key = key.split('[')[0]
        out[key] = out[key] + ',' + value if out.get(key) and value else (out.get(key) or value)
    return out

def sysfs(dev, name):
    try:
        with open(f'/sys/class/net/{dev}/{name}') as f:
            value = f.read().strip()
    except OSError:
        return ''
    return '' if value.startswith('-') else value

def wired_links():
    """Every Ethernet device, Thunderbolt networking included, with its live
    state and the saved profile it uses. Read-only."""
    links = []
    for line in nm('-t', '-f', 'DEVICE,TYPE', 'device', 'status').splitlines():
        dev, _, kind = line.rpartition(':')
        if kind != 'ethernet':
            continue
        d = keyed('GENERAL.STATE,GENERAL.CONNECTION,GENERAL.CON-UUID,GENERAL.HWADDR,'
                  'IP4.ADDRESS,IP4.GATEWAY,IP4.DNS', 'device', 'show', dev)
        state = d.get('GENERAL.STATE', '')
        link = {'device': dev,
                'thunderbolt': dev.startswith('thunderbolt'),
                'state': state.split('(', 1)[-1].rstrip(')') if '(' in state else state,
                'connected': state.startswith('100'),
                'mac': d.get('GENERAL.HWADDR', ''),
                'speed': sysfs(dev, 'speed') if state.startswith('100') else '',
                'mtu': sysfs(dev, 'mtu'),
                'addresses': d.get('IP4.ADDRESS', ''),
                'gateway': d.get('IP4.GATEWAY', ''),
                'dns': d.get('IP4.DNS', ''),
                'profile': d.get('GENERAL.CONNECTION', ''),
                'uuid': d.get('GENERAL.CON-UUID', ''), 'method': '', 'defaultRoute': True}
        if link['uuid']:
            p = keyed('ipv4.method,ipv4.never-default', 'connection', 'show', 'uuid', link['uuid'])
            link.update(method=p.get('ipv4.method', ''),
                        defaultRoute=p.get('ipv4.never-default') != 'yes')
        links.append(link)
    return {'links': links}

def rename(uuid, name):
    """Rename a Wi-Fi or Ethernet profile. Takes effect at once, no reconnect."""
    previous = read_profile(uuid)
    if previous['type'] not in ('802-3-ethernet', '802-11-wireless'):
        raise ValueError('Select a Wi-Fi or Ethernet connection')
    name = name.strip()
    if not name:
        raise ValueError('Enter a name')
    if len(name) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError('Use up to 64 characters, with no control characters')
    if name == previous['name']:
        return {'message': 'The name is unchanged.'}
    for other in nm('-g', 'UUID,NAME', 'connection', 'show').splitlines():
        other_uuid, _, other_name = other.partition(':')
        if other_name == name and other_uuid != uuid:
            raise ValueError('Another connection is already called ' + name)
    nm('connection', 'modify', 'uuid', uuid, 'connection.id', name)
    return {'message': 'Renamed to ' + name + '.'}

def current_static(uuid):
    profile = read_profile(uuid)
    if profile['type'] not in ('802-3-ethernet', '802-11-wireless'):
        raise ValueError('Select a Wi-Fi or Ethernet connection')
    active = nm('-g', 'UUID', 'connection', 'show', '--active').splitlines()
    if uuid not in active:
        raise ValueError('Connect to this network first to use its current subnet')
    def live(field):
        return nm('-g', field, 'connection', 'show', 'uuid', uuid).splitlines()
    addresses = live('IP4.ADDRESS')
    if not addresses:
        raise ValueError('This connection has no current IPv4 address')
    parsed = [ipaddress.IPv4Interface(value) for value in addresses]
    gateway = live('IP4.GATEWAY')
    dns = live('IP4.DNS')
    profile.update(method='manual', addresses=','.join(str(value) for value in parsed),
                   gateway=gateway[0] if gateway else '', dns=','.join(dns),
                   message='Filled from current subnet ' + str(parsed[0].network) + '. Review, then save.')
    return profile

def public_ip():
    request = urllib.request.Request('https://api.ipify.org?format=json',
                                     headers={'User-Agent': 'Netdeck/1.0.0'})
    opener = urllib.request.build_opener(NoRedirectHandler)
    with opener.open(request, timeout=8) as response:
        data = json.loads(response.read(1024))
    return {'ip': str(ipaddress.IPv4Address(data['ip']))}

def main():
    action = sys.argv[1]
    if action == 'public':
        return public_ip()
    if action == 'list':
        profiles = []
        for uuid in nm('-g', 'UUID', 'connection', 'show').splitlines():
            p = read_profile(uuid)
            if p['type'] in ('802-3-ethernet', '802-11-wireless'):
                profiles.append({'value': uuid, 'label': p['name']})
        active = nm('-g', 'UUID', 'connection', 'show', '--active').splitlines()
        selected = next((p['value'] for p in profiles if p['value'] in active), profiles[0]['value'] if profiles else '')
        return {'profiles': profiles, 'selected': read_profile(selected) if selected else None}
    if action == 'read':
        return read_profile(sys.argv[2])
    if action == 'rename':
        return rename(sys.argv[2], sys.argv[3])
    if action == 'wired':
        return wired_links()
    if action == 'current':
        return current_static(sys.argv[2])
    if action == 'save':
        uuid, method, addresses, gateway, dns = sys.argv[2:]
        previous = read_profile(uuid)
        if previous['type'] not in ('802-3-ethernet', '802-11-wireless'):
            raise ValueError('Select a Wi-Fi or Ethernet connection')
        if method not in ('auto', 'manual'):
            raise ValueError('Select DHCP or Static')
        addresses = addresses.strip()
        gateway = gateway.strip()
        dns = dns.strip()
        if method == 'manual':
            if not addresses:
                raise ValueError('Enter an IPv4 address with prefix, such as 192.168.1.50/24')
            for value in addresses.split(','):
                if '/' not in value or ipaddress.ip_interface(value.strip()).version != 4:
                    raise ValueError('Use IPv4 addresses with a prefix, such as 192.168.1.50/24')
            if gateway and ipaddress.ip_address(gateway).version != 4:
                raise ValueError('Gateway must be an IPv4 address')
        else:
            addresses = gateway = ''
        for value in dns.split(',') if dns else []:
            if ipaddress.ip_address(value.strip()).version != 4:
                raise ValueError('Use IPv4 DNS addresses separated by commas')
        nm('connection', 'modify', 'uuid', uuid, 'ipv4.method', method,
           'ipv4.addresses', addresses, 'ipv4.gateway', gateway,
           'ipv4.dns', dns, 'ipv4.ignore-auto-dns', 'yes' if dns else 'no')
        return {'message': 'Saved. Changes take effect when this connection reconnects.'}
    raise ValueError('Unknown action')

if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except Exception as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(1)
