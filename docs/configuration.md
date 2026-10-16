# Minimal dual-stack configuration

All commands here run as root **inside the provisioned container**. Nothing in
this document runs during image creation. Replace the example keys, addresses,
interface names and endpoint with your own. Keep a `pct exec` recovery path open
when changing remote networking or firewall policy.

## 1. Keys and tunnel

Generate keys on each endpoint; private keys must never enter the image or Git:

```sh
install -d -m 700 /etc/wireguard
(umask 077; wg genkey > /etc/wireguard/private.key)
wg pubkey < /etc/wireguard/private.key
```

Keep the printed **public** key to configure the other endpoint. Create
`/etc/wireguard/wg0.conf` with mode 0600. Insert the actual private key securely;
`PrivateKey` expects its base64 value, not a filename.

```ini
[Interface]
Address = 10.66.0.1/24, fd66:66::1/64
ListenPort = 51820
PrivateKey = REPLACE_WITH_CONTAINER_PRIVATE_KEY

[Peer]
PublicKey = REPLACE_WITH_CLIENT_PUBLIC_KEY
AllowedIPs = 10.66.0.2/32, fd66:66::2/128
```

On a client with wg-quick, use its own private key and the container's public key:

```ini
[Interface]
Address = 10.66.0.2/32, fd66:66::2/128
PrivateKey = REPLACE_WITH_CLIENT_PRIVATE_KEY

[Peer]
PublicKey = REPLACE_WITH_CONTAINER_PUBLIC_KEY
Endpoint = REPLACE_WITH_REACHABLE_HOST_OR_IP:51820
AllowedIPs = 10.66.0.0/24, fd66:66::/64
PersistentKeepalive = 25
```

Keepalive is useful for clients behind NAT; omit it if unnecessary. For an IPv6
literal endpoint, use `[IPv6_ADDRESS]:51820`. Permit UDP 51820 through any existing
container, PVE and upstream firewall and forward that port to the container if it
is behind NAT. SSH port 22 must remain reachable from your management network.

No `DNS=` is used: DNS policy belongs to the operator (wg-quick's DNS helper is
not installed). No `SaveConfig` is set; do not enable it when managing files
externally. `wg-quick down` must not overwrite your configuration. There are no
firewall PostUp/PostDown commands hidden in the tunnel file.

For a tunnel terminating at this container, forwarding is unnecessary:

```sh
chmod 600 /etc/wireguard/wg0.conf
wg-quick up wg0
wg show wg0
# After configuring and starting the client, test from the client:
# ping 10.66.0.1
# ping -6 fd66:66::1
wg-quick down wg0
```

If ping is unavailable on the client, use its equivalent. WireGuard is silent
without authenticated peer traffic: `ip link` alone does not prove a handshake.

## 2. Optional routed forwarding

Only do this when the container should route traffic beyond itself. Create
`/etc/sysctl.d/90-wireguard-routing.conf`:

```ini
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
```

Load just this file without reapplying unrelated operator settings:

```sh
sysctl -p /etc/sysctl.d/90-wireguard-routing.conf
```

systemd-sysctl applies it on subsequent boots. If IPv6 uplink routing relies on
router advertisements, forwarding normally disables RA acceptance. In that case
also set `net.ipv6.conf.eth0.accept_ra = 2` (adapt the interface), then confirm
`ip -6 route`. Static IPv6 routes do not require this setting. Ensure IPv6 is
enabled in the PVE network configuration and permitted by the host.

For routed operation, configure persistent upstream routes for `10.66.0.0/24`
and `fd66:66::/64` via the container's LAN IPv4/IPv6 addresses. Use your router's
normal persistent configuration. Add destination LAN prefixes to the client's
`AllowedIPs` if clients should reach them. On the server, each client's
`AllowedIPs` stays limited to that client's source addresses or delegated subnet.
`AllowedIPs` authenticates sources and selects tunnel routes; it does not replace
a firewall. Choose non-overlapping prefixes.

The ULA IPv6 example is suitable for private routed networks. IPv6 Internet
access normally needs a globally routed/delegated prefix with return routes;
substitute it throughout. Do not expect ULA addresses to reach the public Internet
without a deliberate translation design. MTU depends on the outer path: start
with wg-quick's automatic value and investigate ICMP/ICMPv6 path-MTU feedback if
large packets fail.

## 3. Optional scoped nftables policy

If you already manage a firewall, integrate rules into that system and its
existing persistence mechanism. An accept in a separate base chain cannot
bypass a drop in another chain. Permit tunnel UDP ingress, selected forwarding,
return traffic, management SSH and required ICMP/ICMPv6 (including neighbor
discovery and packet-too-big). Do not replace an existing ruleset blindly.

For a new setup, the following optional file owns **only** `inet wg_vpn`.
It limits forwarded traffic involving wg0, permits other traffic unchanged and
is **not** a complete host INPUT firewall. Adapt eth0 and the tunnel prefixes.
Create `/etc/nftables.d/wireguard.nft` after creating its parent directory:

```nft
table inet wg_vpn {
    chain forward {
        type filter hook forward priority filter; policy accept;
        ct state invalid iifname "wg0" drop
        iifname "wg0" oifname "eth0" ip saddr 10.66.0.0/24 accept
        iifname "wg0" oifname "eth0" ip6 saddr fd66:66::/64 accept
        oifname "wg0" ct state established,related accept
        iifname "wg0" drop
        oifname "wg0" drop
    }
}
```

This permits clients to initiate connections via eth0 and blocks new forwarded
connections into wg0. Narrow destination networks/ports as appropriate. It does
not permit client-to-client routing. It never runs `flush ruleset`.

Debian's standard `nftables.service` flushes the **entire** ruleset on stop. Leave
it disabled for this isolated example. If another firewall manager already uses
it, leave that manager alone and integrate there instead of adding this unit.
To persist only this example, create `/etc/systemd/system/wg-firewall.service`:

```ini
[Unit]
Description=Operator WireGuard forwarding rules
Before=wg-quick@wg0.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.d/wireguard.nft
ExecStop=/usr/sbin/nft delete table inet wg_vpn

[Install]
WantedBy=multi-user.target
```

Do not use the table name for other purposes or load the file twice: loading
an existing table can append rules. The unit owns loading/deleting its table;
use `systemctl restart wg-firewall` to apply changes. Check first:

```sh
nft -c -f /etc/nftables.d/wireguard.nft
systemctl daemon-reload
systemctl enable --now wg-firewall.service
```

If using this unit, create the directory
`/etc/systemd/system/wg-quick@wg0.service.d/` and add `firewall.conf` there:

```ini
[Unit]
Requires=wg-firewall.service
After=wg-firewall.service
```

This prevents tunnel startup when firewall loading fails. Stopping a required
firewall also stops the tunnel; after a firewall restart, explicitly start the
tunnel again. For rule changes requiring continuous enforcement, use your
existing firewall manager's transactional update mechanism instead.

## 4. Optional NAT

Routing preserves peer addresses and is the default design described above.
For IPv4 Internet access where upstream return routes are unavailable, append
this chain **inside** the `inet wg_vpn` table before starting the firewall unit:

```nft
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname "eth0" ip saddr 10.66.0.0/24 masquerade
    }
```

This translates only the selected IPv4 tunnel subnet leaving eth0. To route
all client IPv4 Internet traffic, change the client's IPv4 AllowedIPs to
`0.0.0.0/0`; choose its DNS configuration separately. Keep the IPv6 prefix route
unless global IPv6 routing has been configured. Full dual-stack routing uses
`0.0.0.0/0, ::/0` and requires working egress and return paths for both families.

IPv6 should normally use routed global prefixes. If you deliberately need NAT66,
the corresponding explicit rule is `oifname "eth0" ip6 saddr fd66:66::/64
masquerade` in that same NAT chain. It needs working IPv6 uplink routing and
forwarding too. Neither NAT rule opens a firewall nor replaces routing. NAT is
never enabled in the shipped image.

## 5. Boot persistence and updates

After a successful manual test, enable only the tunnel you actually configured:

```sh
systemctl daemon-reload
systemctl enable --now wg-quick@wg0.service
systemctl status ssh wg-quick@wg0
wg show wg0
nft list ruleset
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
ip -4 route
ip -6 route
```

`network-online.target` is a boot ordering point, not proof that a remote peer is
reachable. WireGuard can wait for traffic; no polling reconciler is needed.

For changes to peers/keys that do not alter addresses/routes, the packaged unit
supports `systemctl reload wg-quick@wg0` using `wg syncconf` and `wg-quick strip`.
Use `systemctl restart wg-quick@wg0` when changing addresses or routes. External
management should write mode-0600 files atomically and choose reload/restart
explicitly. Do not enable SaveConfig.

Reboot the container during a maintenance window. Verify SSH returns, host-key
fingerprint and machine-id remain unchanged, sysctls/rules are restored and a real
peer performs a new handshake and transfers IPv4/IPv6 traffic in both directions.
Check `journalctl -b -u ssh -u wg-quick@wg0 -u wg-firewall` for failures.

To undo this optional setup, stop/disable wg-quick first, then wg-firewall (which
deletes only its own table), remove their operator-created configuration, remove
the sysctl file and explicitly restore previous forwarding/RA values. Removing
a sysctl file alone does not change the running kernel setting.

References: Debian [wg-quick(8)](https://manpages.debian.org/trixie/wireguard-tools/wg-quick.8.en.html),
[wg(8)](https://manpages.debian.org/trixie/wireguard-tools/wg.8.en.html),
[nft(8)](https://manpages.debian.org/trixie/nftables/nft.8.en.html) and
[sysctl.d(5)](https://manpages.debian.org/trixie/systemd/sysctl.d.5.en.html).
