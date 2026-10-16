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

No `DNS=` is used: DNS policy belongs to the operator. Alpine's wg-quick package
depends on openresolv; using `DNS=` would deliberately change resolver settings. No `SaveConfig` is set; do not enable it when managing files
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

The image's `10-wireguard-defaults.conf` explicitly disables IPv4/IPv6 forwarding.
Only opt in when the container should route traffic beyond itself. Create
`/etc/sysctl.d/90-wireguard-routing.conf`:

```ini
net.ipv4.ip_forward = 1
net.ipv4.conf.default.forwarding = 1
net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.default.forwarding = 1
```

The default-interface settings also cover interfaces created later, including wg0.
Load just this file without reapplying unrelated operator settings:

```sh
sysctl -p /etc/sysctl.d/90-wireguard-routing.conf
```

The enabled OpenRC sysctl service applies the `10-` baseline before this `90-`
operator override on subsequent boots. It runs before bootmisc and networking.
Alpine then reads `/etc/sysctl.conf`, followed by `/run/sysctl.d/*.conf`; those
can override both files. Review them and interface `forward-ipv4`/`forward-ipv6`
options or pre/post-up hooks when troubleshooting. There is no post-network
reset, timer or reconciler. A deliberately configured VPN service may also set
the values at start, after the boot baseline. If IPv6 uplink routing relies on
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

## 3. Optional nftables policy

If another tool already manages your firewall, integrate rules and persistence
there. An accept in a separate base chain cannot bypass a drop in another chain.
Permit tunnel UDP ingress, selected forwarding and return traffic, management SSH
and ICMP/ICMPv6 (including neighbor discovery and packet-too-big).

For a new deployment where you deliberately assign firewall ownership to Alpine's
packaged nftables service, use the following `/etc/nftables.nft`. It owns only
`inet wg_vpn` on load, permits unrelated traffic unchanged and is **not a complete
host INPUT firewall**. Adapt eth0 and the tunnel prefixes. `destroy` makes replacing
this table idempotent, including its first load; it does not delete other tables.

```nft
destroy table inet wg_vpn
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
not permit client-to-client routing or open UDP/SSH in another firewall.

**Alpine's regular nftables service flushes the entire ruleset on stop** (and on
the stop phase of restart). Use it only when it owns firewall lifecycle. The
shipped image leaves it disabled. If another firewall manager exists, leave that
manager in charge; do not enable or stop this service over it. No custom firewall
service is installed by this template.

In `/etc/conf.d/nftables`, explicitly keep these settings:

```sh
rules_file="/etc/nftables.nft"
save_on_stop="no"
enable_forwarding="no"
```

Forwarding belongs to the explicit sysctl configuration, not to the firewall's
start/stop behavior. Validate and enable the service only after reviewing the
current `nft list ruleset` and assigning it ownership:

```sh
nft -c -f /etc/nftables.nft
rc-update add nftables boot
rc-service nftables start
```

To require this firewall before the configured WireGuard instance starts, create
`/etc/conf.d/wg-quick.wg0`:

```sh
rc_need="nftables"
```

This adds an OpenRC startup dependency to the package's `need net`. For an existing
firewall manager use its own appropriate service/dependencies instead. This is
startup ordering, not continuous enforcement if someone later removes rules.

Use `rc-service nftables reload` for reviewed rule changes: the packaged reload
calls start, and the example atomically replaces only its own table. Do not use
stop/restart when preserving other tables matters. Do not enable save_on_stop or
use `nftables save` when managing files externally. An existing manager may have
different reload semantics; follow that manager's documentation.

## 4. Optional NAT

Routing preserves peer addresses and is the default design described above.
For IPv4 Internet access where upstream return routes are unavailable, append
this chain **inside** the `inet wg_vpn` table before starting the firewall service:

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

## 5. OpenRC boot persistence and updates

Use Alpine's packaged multi-instance service, not a new init script:

```sh
ln -s /etc/init.d/wg-quick /etc/init.d/wg-quick.wg0
rc-update add wg-quick.wg0 default
rc-service wg-quick.wg0 start
rc-service wg-quick.wg0 status
wg show wg0
nft list ruleset
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
ip -4 route
ip -6 route
```

If you created the instance link earlier, keep it rather than replacing it. The
native service requires networking and uses DNS if available. A started network
service is not proof of remote peer reachability. DNS resolution of the configured
endpoint must work at startup. WireGuard needs no polling reconciler.

For peer/key changes without address or route changes, use
`rc-service wg-quick.wg0 reload`: the packaged service uses `wg syncconf` and
`wg-quick strip`. For address/route changes, use
`rc-service wg-quick.wg0 restart` in a maintenance window. External tools should
write mode-0600 files atomically and explicitly choose reload/restart. Do not
set SaveConfig.

Reboot the container during a maintenance window. Verify SSH returns, host-key
fingerprints and machine-id remain stable, sysctls/rules are restored, and a real
peer makes a new handshake and transfers IPv4/IPv6 traffic in both directions.
Use `rc-status -a`, `rc-service sshd status`, `wg show` and
`tail -n 100 /var/log/messages` for diagnostics.

To undo this optional setup, stop/disable the tunnel first with `rc-service` and
`rc-update del wg-quick.wg0 default`. Remove only operator-created configuration
and your own nftables table. Remember that stopping Alpine's nftables service
flushes all tables. Remove the sysctl file and explicitly restore previous
forwarding/RA values; removing a file alone does not reset live kernel settings.

References: Alpine's [WireGuard guide](https://wiki.alpinelinux.org/wiki/Configure_a_Wireguard_interface_%28wg%29),
[OpenRC guide](https://wiki.alpinelinux.org/wiki/OpenRC), and the packaged
[WireGuard](https://gitlab.alpinelinux.org/alpine/aports/-/blob/3.24-stable/main/wireguard-tools/wg-quick.initd)
and [nftables](https://gitlab.alpinelinux.org/alpine/aports/-/blob/3.24-stable/main/nftables/nftables.initd)
service definitions.
