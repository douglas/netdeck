#!/usr/bin/env python3
"""Tests for settings.py validation.

settings.py is the only part of this plugin that can change the machine's
network configuration, and it is also the part with no test coverage. These
tests exercise the validation and the nmcli argument construction against a
fake nmcli, so no real connection is ever touched.

Run: python3 test/settings.test.py
"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import settings  # noqa: E402


class FakeNmcli:
    """Stand-in for subprocess.run that records calls and replays output.

    Keyed on the argv after `nmcli --escape no`, so a test can name the
    response by the flags it cares about (`-g connection.id,connection.type`)
    without restating the whole command.
    """

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        rest = list(args)
        if rest[:1] == ['nmcli']:
            rest = rest[1:]
        if rest[:2] == ['--escape', 'no']:
            rest = rest[2:]
        joined = ' '.join(rest)
        # Longest matching prefix wins, so a test can key on the field list
        # (`-g connection.id,connection.type`) without naming the subcommand.
        out = self.responses.get(joined, '')
        best = ''
        for key, value in self.responses.items():
            if joined.startswith(key) and len(key) > len(best):
                best, out = key, value
        if isinstance(out, Exception):
            return subprocess.CompletedProcess(args, 1, '', str(out))
        return subprocess.CompletedProcess(args, 0, out, '')


class ValidationTests(unittest.TestCase):
    """The save path must refuse anything that would break connectivity."""

    def save(self, uuid, method, addresses, gateway, dns, previous=None):
        responses = {}
        if previous is not None:
            responses['-g connection.id,connection.type'] = (
                'TestNet\n%s\n%s\n%s\n%s\n%s\n' % (
                    previous.get('type', '802-11-wireless'),
                    previous.get('method', 'auto'),
                    previous.get('addresses', ''),
                    previous.get('gateway', ''),
                    previous.get('dns', '')))
        fake = FakeNmcli(responses)
        with mock.patch.object(settings.subprocess, 'run', fake):
            with mock.patch.object(sys, 'argv',
                                   ['settings.py', 'save', uuid, method, addresses, gateway, dns]):
                try:
                    result = settings.main()
                    return result, fake
                except ValueError as e:
                    return {'error': str(e)}, fake

    def test_rejects_a_non_network_profile(self):
        result, _ = self.save('u1', 'auto', '', '', '',
                              previous={'type': 'vpn'})
        self.assertIn('error', result)

    def test_rejects_an_unknown_method(self):
        result, _ = self.save('u1', 'dhcpv6', '', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)
        self.assertIn('DHCP', result['error'])

    def test_rejects_manual_with_no_address(self):
        result, _ = self.save('u1', 'manual', '', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)
        self.assertIn('prefix', result['error'])

    def test_rejects_an_address_without_a_prefix(self):
        result, _ = self.save('u1', 'manual', '192.168.1.50', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)

    def test_rejects_an_ipv6_address(self):
        result, _ = self.save('u1', 'manual', '2001:db8::1/64', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)

    def test_rejects_a_malformed_address(self):
        result, _ = self.save('u1', 'manual', '999.1.1.1/24', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)

    def test_rejects_an_ipv6_gateway(self):
        result, _ = self.save('u1', 'manual', '192.168.1.50/24', '2001:db8::1', '',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)

    def test_rejects_malformed_dns(self):
        result, _ = self.save('u1', 'manual', '192.168.1.50/24', '', 'not-an-ip',
                              previous={'type': '802-11-wireless'})
        self.assertIn('error', result)

    def test_accepts_a_valid_manual_profile(self):
        result, fake = self.save('u1', 'manual', '192.168.1.50/24', '192.168.1.1', '1.1.1.1',
                                 previous={'type': '802-11-wireless'})
        self.assertNotIn('error', result)
        self.assertTrue(any('connection' in c and 'modify' in c for c in fake.calls))

    def test_accepts_multiple_addresses(self):
        result, _ = self.save('u1', 'manual', '192.168.1.50/24,192.168.1.51/24', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertNotIn('error', result)

    def test_auto_clears_the_static_fields(self):
        # Switching back to DHCP must not leave a stale address behind, or the
        # profile fails to renew.
        result, fake = self.save('u1', 'auto', '192.168.1.50/24', '192.168.1.1', '',
                                 previous={'type': '802-11-wireless'})
        self.assertNotIn('error', result)
        modify = next(c for c in fake.calls if 'modify' in c)
        self.assertEqual(modify[modify.index('ipv4.addresses') + 1], '')
        self.assertEqual(modify[modify.index('ipv4.gateway') + 1], '')

    def test_ignore_auto_dns_follows_the_dns_field(self):
        _, fake = self.save('u1', 'manual', '192.168.1.50/24', '', '1.1.1.1,8.8.8.8',
                            previous={'type': '802-11-wireless'})
        modify = next(c for c in fake.calls if 'modify' in c)
        self.assertEqual(modify[modify.index('ipv4.ignore-auto-dns') + 1], 'yes')

        _, fake = self.save('u1', 'auto', '', '', '',
                            previous={'type': '802-11-wireless'})
        modify = next(c for c in fake.calls if 'modify' in c)
        self.assertEqual(modify[modify.index('ipv4.ignore-auto-dns') + 1], 'no')

    def test_gateway_is_optional_in_manual(self):
        result, _ = self.save('u1', 'manual', '192.168.1.50/24', '', '',
                              previous={'type': '802-11-wireless'})
        self.assertNotIn('error', result)


class ReadTests(unittest.TestCase):
    def test_read_profile_pads_missing_fields(self):
        fake = FakeNmcli({'-g connection.id,connection.type': 'TestNet\n802-3-ethernet\n'})
        with mock.patch.object(settings.subprocess, 'run', fake):
            with mock.patch.object(sys, 'argv', ['settings.py', 'read', 'u1']):
                profile = settings.main()
        self.assertEqual(profile['name'], 'TestNet')
        self.assertEqual(profile['method'], '')
        self.assertEqual(profile['addresses'], '')
        self.assertEqual(profile['uuid'], 'u1')

    def test_read_profile_surfaces_an_nmcli_failure(self):
        fake = FakeNmcli({'-g connection.id,connection.type': 'Error: no such connection'})
        # A non-zero return is what nmcli gives on failure; the helper raises.
        def boom(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'Error: unknown connection')
        with mock.patch.object(settings.subprocess, 'run', boom):
            with mock.patch.object(sys, 'argv', ['settings.py', 'read', 'nope']):
                with self.assertRaises(ValueError):
                    settings.main()


class WiredTests(unittest.TestCase):
    def wired(self, responses, sysfs=None):
        fake = FakeNmcli(responses)
        values = sysfs or {}
        with mock.patch.object(settings.subprocess, 'run', fake), \
                mock.patch.object(settings, 'sysfs', lambda dev, name: values.get((dev, name), '')):
            with mock.patch.object(sys, 'argv', ['settings.py', 'wired']):
                return settings.main(), fake

    def test_lists_ethernet_and_thunderbolt_but_not_wifi(self):
        result, fake = self.wired({
            '-t -f DEVICE,TYPE device status': 'wlp1s0:wifi\nthunderbolt0:ethernet\nenp2s0:ethernet\nlo:loopback\n',
            '-t -f GENERAL.STATE,GENERAL.CONNECTION,GENERAL.CON-UUID,GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show thunderbolt0': (
                'GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:tb-link\nGENERAL.CON-UUID:u-tb\n'
                'GENERAL.HWADDR:02:AA:BB:CC:DD:EE\nIP4.ADDRESS[1]:10.0.0.1/24\nIP4.ADDRESS[2]:10.0.0.9/24\nIP4.GATEWAY:\n'),
            '-t -f GENERAL.STATE,GENERAL.CONNECTION,GENERAL.CON-UUID,GENERAL.HWADDR,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show enp2s0': (
                'GENERAL.STATE:20 (unavailable)\nGENERAL.CONNECTION:\nGENERAL.CON-UUID:\nGENERAL.HWADDR:9C:00:00:00:00:01\n'),
            '-t -f ipv4.method,ipv4.never-default connection show uuid u-tb': 'ipv4.method:manual\nipv4.never-default:yes\n',
        }, sysfs={('thunderbolt0', 'speed'): '40000', ('thunderbolt0', 'mtu'): '65520', ('enp2s0', 'mtu'): '1500'})
        tb, eth = result['links']
        self.assertEqual([tb['device'], eth['device']], ['thunderbolt0', 'enp2s0'])
        self.assertTrue(tb['thunderbolt'])
        self.assertEqual(tb['addresses'], '10.0.0.1/24,10.0.0.9/24')
        self.assertEqual(tb['mac'], '02:AA:BB:CC:DD:EE')
        self.assertEqual((tb['state'], tb['speed'], tb['mtu']), ('connected', '40000', '65520'))
        self.assertEqual((tb['method'], tb['defaultRoute']), ('manual', False))
        self.assertEqual((eth['state'], eth['uuid'], eth['speed']), ('unavailable', '', ''))
        # Read-only: never modifies a connection.
        self.assertFalse(any('modify' in c for c in fake.calls))


class RenameTests(unittest.TestCase):
    def rename(self, name, kind='802-3-ethernet', others='u1:old-name\nu2:taken\n'):
        fake = FakeNmcli({
            '-g connection.id,connection.type': 'old-name\n%s\nmanual\n\n\n\n' % kind,
            '-g UUID,NAME connection show': others,
        })
        with mock.patch.object(settings.subprocess, 'run', fake):
            with mock.patch.object(sys, 'argv', ['settings.py', 'rename', 'u1', name]):
                try:
                    return settings.main(), fake
                except ValueError as e:
                    return {'error': str(e)}, fake

    def modify(self, fake):
        return [c for c in fake.calls if 'modify' in c]

    def test_renames_with_connection_id_only(self):
        result, fake = self.rename('  thunderbolt-peer  ')
        self.assertNotIn('error', result)
        (call,) = self.modify(fake)
        self.assertEqual(call[call.index('uuid') + 1], 'u1')
        self.assertEqual(call[call.index('connection.id') + 1], 'thunderbolt-peer')
        self.assertNotIn('ipv4.method', call)

    def test_rejects_an_empty_name(self):
        result, fake = self.rename('   ')
        self.assertIn('error', result)
        self.assertEqual(self.modify(fake), [])

    def test_rejects_control_characters_and_long_names(self):
        for name in ('bad\nname', 'x' * 65):
            result, fake = self.rename(name)
            self.assertIn('error', result)
            self.assertEqual(self.modify(fake), [])

    def test_rejects_a_name_another_connection_has(self):
        result, fake = self.rename('taken')
        self.assertIn('already', result['error'])
        self.assertEqual(self.modify(fake), [])

    def test_unchanged_name_is_a_no_op(self):
        result, fake = self.rename('old-name')
        self.assertNotIn('error', result)
        self.assertEqual(self.modify(fake), [])

    def test_rejects_a_non_network_profile(self):
        result, fake = self.rename('new', kind='vpn')
        self.assertIn('error', result)
        self.assertEqual(self.modify(fake), [])


class DetailsTests(unittest.TestCase):
    """`details` stands in for `omarchy-network-status --verbose` when pings
    are off, so it must report the same fields and never run ping."""

    ROUTE = json.dumps([{'dev': 'wlan0', 'gateway': '192.168.1.1', 'prefsrc': '192.168.1.50'}])
    ADDR = json.dumps([{'addr_info': [{'family': 'inet6', 'prefixlen': 64},
                                      {'family': 'inet', 'prefixlen': 24}]}])
    IW = ('Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n'
          '\tSSID: Caf\\xc3\\xa9\n\tfreq: 5180.0\n\tsignal: -48 dBm\n'
          '\ttx bitrate: 866.7 MBit/s VHT-MCS 9\n\trx bitrate: 650.0 MBit/s\n')
    SYSFS = {'statistics/rx_bytes': '100', 'statistics/tx_bytes': '200',
             'speed': '2500', 'duplex': 'full'}

    def details(self, responses, wireless):
        fake = FakeNmcli(responses)
        with mock.patch.object(settings.subprocess, 'run', fake), \
                mock.patch.object(settings, 'sysfs', lambda dev, name: self.SYSFS.get(name, '')), \
                mock.patch.object(settings.os.path, 'isdir', lambda path: wireless):
            return settings.link_details(), fake

    def test_reports_wifi_fields_without_pinging(self):
        result, fake = self.details({'ip -j route get': self.ROUTE, 'ip -j addr show': self.ADDR,
                                     'iw dev wlan0 link': self.IW}, wireless=True)
        self.assertEqual(result, {'iface': 'wlan0', 'ip': '192.168.1.50', 'prefix': '24',
                                  'gateway': '192.168.1.1', 'rx_bytes': '100', 'tx_bytes': '200',
                                  'type': 'wifi', 'ssid': 'Caf\\xc3\\xa9', 'signal_dbm': '-48',
                                  'freq': '5180.0', 'bitrate': '866.7 MBit/s'})
        self.assertNotIn('ping', {args[0] for args in fake.calls})

    def test_reports_ethernet_speed_and_duplex(self):
        result, _ = self.details({'ip -j route get': self.ROUTE, 'ip -j addr show': self.ADDR}, wireless=False)
        self.assertEqual(result['type'], 'ethernet')
        self.assertEqual((result['speed'], result['duplex']), ('2500', 'full'))

    def test_no_route_reports_nothing(self):
        result, _ = self.details({'ip -j route get': ValueError('unreachable')}, wireless=False)
        self.assertEqual(result, {})


class PublicIpTests(unittest.TestCase):
    def test_public_ip_validates_the_address(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return json.dumps({'ip': '203.0.113.7'}).encode()

        with mock.patch.object(settings.urllib.request, 'build_opener', lambda *a: mock.Mock(open=lambda *a, **k: FakeResponse())):
            self.assertEqual(settings.public_ip(), {'ip': '203.0.113.7'})

    def test_public_ip_rejects_a_non_ipv4_payload(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return json.dumps({'ip': 'not-an-ip'}).encode()

        with mock.patch.object(settings.urllib.request, 'build_opener', lambda *a: mock.Mock(open=lambda *a, **k: FakeResponse())):
            with self.assertRaises(ValueError):
                settings.public_ip()

    def test_public_ip_refuses_redirects_without_buffering(self):
        handler = settings.NoRedirectHandler()

        class FakeFp:
            def read(self, *a):
                raise AssertionError('Redirect body should never be read')

            def close(self):
                pass

        req = settings.urllib.request.Request('https://api.ipify.org?format=json')
        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(settings.urllib.error.HTTPError) as cm:
                getattr(handler, f'http_error_{code}')(req, FakeFp(), code, 'Redirect', {'location': 'https://evil.com'})
            self.assertEqual(cm.exception.code, code)
            cm.exception.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
