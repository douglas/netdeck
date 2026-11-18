# Changelog

## 1.0.0

- Released as Netdeck (`io.github.douglas.netdeck`), from Rohi Rikman's
  `rohi.network`. The *Connected only* state moves to `netdeck.json`.
- Wired links screen (`e`, or *Wired links ›*): every Ethernet device,
  Thunderbolt networking included, with its state, IPv4 address and mode,
  gateway, DNS, whether it can be the default route, link speed, MTU and MAC.
  Read-only; *Edit IP settings ›* opens that link's profile in IP settings.
  Styled like the main screen (hero, detail grid, section header), with a
  copy button beside each IP address.
  A *Connected only* switch in its header hides links with no cable or peer;
  it is remembered in `$XDG_STATE_HOME/omarchy/rohi.network.json`.
  The pencil beside a link's name renames its profile (`connection.id`), at
  once and without a reconnect; names other connections use are refused.
- README screenshots of the network panel and IP settings, rendered from the
  real QML with synthetic data by `tools/render-previews.py`.

## 0.2.0

- Public IPv4 lookup through ipify.org, on demand only, with the contacting
  service named on the button.
- IP settings screen (`s`, or *IP settings ›*): list the saved Wi-Fi and
  Ethernet profiles, switch one between DHCP and a fixed address, set gateway
  and DNS, and pre-fill from the current subnet with *Use current IP as fixed*.
- `settings.py` holds every `nmcli` call behind one JSON-per-invocation
  interface, with validation that refuses IPv6, a missing prefix, and any
  profile that is not Wi-Fi or Ethernet.
- Tests: 84 checks over `Model.js`, the save path, the manifest contract, and
  live `nmcli` output.

## 0.1.0

- Cloned from `omarchy.network`. Identical to stock at this point.
