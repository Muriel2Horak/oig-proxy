# socket — Low-level networking interface &#8212; Python 3.14.3 documentation

> Source: https://docs.python.org/3/library/socket.html#socket.socket.settimeout
> Cached: 2026-02-18T03:56:44.786Z

---

Theme
    
        Auto
        Light
        Dark
    

  
    ### [Table of Contents](../contents.html)

    

[`socket` — Low-level networking interface](#)

- [Socket families](#socket-families)

[Module contents](#module-contents)

- [Exceptions](#exceptions)

- [Constants](#constants)

[Functions](#functions)

- [Creating sockets](#creating-sockets)

- [Other functions](#other-functions)

- [Socket Objects](#socket-objects)

[Notes on socket timeouts](#notes-on-socket-timeouts)

- [Timeouts and the `connect` method](#timeouts-and-the-connect-method)

- [Timeouts and the `accept` method](#timeouts-and-the-accept-method)

- [Example](#example)

  
  
    #### Previous topic

    [Developing with asyncio](asyncio-dev.html)
  
  
    #### Next topic

    [`ssl` — TLS/SSL wrapper for socket objects](ssl.html)
  
  
    ### This page

    

      - [Report a bug](../bugs.html)

      
        Show source
        
      
      
    

  
        
    

  
    
      ### Navigation

      

        
          [index](../genindex.html)
        
          [modules](../py-modindex.html) |
        
          [next](ssl.html) |
        
          [previous](asyncio-dev.html) |

          - 

          - [Python](https://www.python.org/) &#187;

          
            
            
          
          
              
          
    
      [3.14.3 Documentation](../index.html) &#187;
    

          - [The Python Standard Library](index.html) &#187;

          - [Networking and Interprocess Communication](ipc.html) &#187;

        - [`socket` — Low-level networking interface]()

                
                    

    
        
          
          
        
    
                     |
                
            

    Theme
    
        Auto
        Light
        Dark
    
 |
            
      

        

    
      
        
          
            
  
# `socket` — Low-level networking interface[¶](#module-socket)

**Source code:** [Lib/socket.py](https://github.com/python/cpython/tree/3.14/Lib/socket.py)

This module provides access to the BSD *socket* interface. It is available on
all modern Unix systems, Windows, MacOS, and probably additional platforms.

Note

Some behavior may be platform dependent, since calls are made to the operating
system socket APIs.

[Availability](intro.html#availability): not WASI.

This module does not work or is not available on WebAssembly. See
[WebAssembly platforms](intro.html#wasm-availability) for more information.

The Python interface is a straightforward transliteration of the Unix system
call and library interface for sockets to Python’s object-oriented style: the
[`socket()`](#socket.socket) function returns a *socket object* whose methods implement
the various socket system calls.  Parameter types are somewhat higher-level than
in the C interface: as with `read()` and `write()` operations on Python
files, buffer allocation on receive operations is automatic, and buffer length
is implicit on send operations.

See also

Module [`socketserver`](socketserver.html#module-socketserver)Classes that simplify writing network servers.

Module [`ssl`](ssl.html#module-ssl)A TLS/SSL wrapper for socket objects.

## Socket families[¶](#socket-families)

Depending on the system and the build options, various socket families
are supported by this module.
The address format required by a particular socket object is automatically
selected based on the address family specified when the socket object was
created.  Socket addresses are represented as follows:

The address of an [`AF_UNIX`](#socket.AF_UNIX) socket bound to a file system node
is represented as a string, using the file system encoding and the
`'surrogateescape'` error handler (see [**PEP 383**](https://peps.python.org/pep-0383/)).  An address in
Linux’s abstract namespace is returned as a [bytes-like object](../glossary.html#term-bytes-like-object) with
an initial null byte; note that sockets in this namespace can
communicate with normal file system sockets, so programs intended to
run on Linux may need to deal with both types of address.  A string or
bytes-like object can be used for either type of address when
passing it as an argument.

Changed in version 3.3: Previously, [`AF_UNIX`](#socket.AF_UNIX) socket paths were assumed to use UTF-8
encoding.

Changed in version 3.5: Writable [bytes-like object](../glossary.html#term-bytes-like-object) is now accepted.

A pair `(host, port)` is used for the [`AF_INET`](#socket.AF_INET) address family,
where *host* is a string representing either a hostname in internet domain
notation like `'daring.cwi.nl'` or an IPv4 address like `'100.50.200.5'`,
and *port* is an integer.

For IPv4 addresses, two special forms are accepted instead of a host
address: `''` represents `INADDR_ANY`, which is used to bind to all
interfaces, and the string `'<broadcast>'` represents
`INADDR_BROADCAST`.  This behavior is not compatible with IPv6,
therefore, you may want to avoid these if you intend to support IPv6 with your
Python programs.

For [`AF_INET6`](#socket.AF_INET6) address family, a four-tuple (host, port, flowinfo,
scope_id) is used, where *flowinfo* and *scope_id* represent the `sin6_flowinfo`
and `sin6_scope_id` members in `struct sockaddr_in6` in C.  For
`socket` module methods, *flowinfo* and *scope_id* can be omitted just for
backward compatibility.  Note, however, omission of *scope_id* can cause problems
in manipulating scoped IPv6 addresses.

Changed in version 3.7: For multicast addresses (with *scope_id* meaningful) *address* may not contain
`%scope_id` (or `zone id`) part. This information is superfluous and may
be safely omitted (recommended).

`AF_NETLINK` sockets are represented as pairs `(pid, groups)`.

Linux-only support for TIPC is available using the `AF_TIPC`
address family.  TIPC is an open, non-IP based networked protocol designed
for use in clustered computer environments.  Addresses are represented by a
tuple, and the fields depend on the address type. The general tuple form is
`(addr_type, v1, v2, v3 [, scope])`, where:

*addr_type* is one of `TIPC_ADDR_NAMESEQ`, `TIPC_ADDR_NAME`,
or `TIPC_ADDR_ID`.
*scope* is one of `TIPC_ZONE_SCOPE`, `TIPC_CLUSTER_SCOPE`, and
`TIPC_NODE_SCOPE`.
If *addr_type* is `TIPC_ADDR_NAME`, then *v1* is the server type, *v2* is
the port identifier, and *v3* should be 0.
If *addr_type* is `TIPC_ADDR_NAMESEQ`, then *v1* is the server type, *v2*
is the lower port number, and *v3* is the upper port number.
If *addr_type* is `TIPC_ADDR_ID`, then *v1* is the node, *v2* is the
reference, and *v3* should be set to 0.

A tuple `(interface, )` is used for the [`AF_CAN`](#socket.AF_CAN) address family,
where *interface* is a string representing a network interface name like
`'can0'`. The network interface name `''` can be used to receive packets
from all network interfaces of this family.

[`CAN_ISOTP`](#socket.CAN_ISOTP) protocol requires a tuple `(interface, rx_addr, tx_addr)`
where both additional parameters are unsigned long integer that represent a
CAN identifier (standard or extended).
[`CAN_J1939`](#socket.CAN_J1939) protocol requires a tuple `(interface, name, pgn, addr)`
where additional parameters are 64-bit unsigned integer representing the
ECU name, a 32-bit unsigned integer representing the Parameter Group Number
(PGN), and an 8-bit integer representing the address.

A string or a tuple `(id, unit)` is used for the `SYSPROTO_CONTROL`
protocol of the `PF_SYSTEM` family. The string is the name of a
kernel control using a dynamically assigned ID. The tuple can be used if ID
and unit number of the kernel control are known or if a registered ID is
used.

Added in version 3.3.

[`AF_BLUETOOTH`](#socket.AF_BLUETOOTH) supports the following protocols and address
formats:

[`BTPROTO_L2CAP`](#socket.BTPROTO_L2CAP) accepts a tuple
`(bdaddr, psm[, cid[, bdaddr_type]])` where:

`bdaddr` is a string specifying the Bluetooth address.

`psm` is an integer specifying the Protocol/Service Multiplexer.

`cid` is an optional integer specifying the Channel Identifier.
If not given, defaults to zero.
`bdaddr_type` is an optional integer specifying the address type;
one of [`BDADDR_BREDR`](#socket.BDADDR_BREDR) (default), [`BDADDR_LE_PUBLIC`](#socket.BDADDR_LE_PUBLIC),
[`BDADDR_LE_RANDOM`](#socket.BDADDR_LE_RANDOM).

Changed in version 3.14: Added `cid` and `bdaddr_type` fields.

[`BTPROTO_RFCOMM`](#socket.BTPROTO_RFCOMM) accepts `(bdaddr, channel)` where `bdaddr`
is the Bluetooth address as a string and `channel` is an integer.
[`BTPROTO_HCI`](#socket.BTPROTO_HCI) accepts a format that depends on your OS.

On Linux it accepts an integer `device_id` or a tuple
`(device_id, [channel])` where `device_id`
specifies the number of the Bluetooth device,
and `channel` is an optional integer specifying the HCI channel
([`HCI_CHANNEL_RAW`](#socket.HCI_CHANNEL_RAW) by default).
On FreeBSD, NetBSD and DragonFly BSD it accepts `bdaddr`
where `bdaddr` is the Bluetooth address as a string.

Changed in version 3.2: NetBSD and DragonFlyBSD support added.

Changed in version 3.13.3: FreeBSD support added.

Changed in version 3.14: Added `channel` field.
`device_id` not packed in a tuple is now accepted.

[`BTPROTO_SCO`](#socket.BTPROTO_SCO) accepts `bdaddr` where `bdaddr` is
the Bluetooth address as a string or a [`bytes`](stdtypes.html#bytes) object.
(ex. `'12:23:34:45:56:67'` or `b'12:23:34:45:56:67'`)

Changed in version 3.14: FreeBSD support added.

[`AF_ALG`](#socket.AF_ALG) is a Linux-only socket based interface to Kernel
cryptography. An algorithm socket is configured with a tuple of two to four
elements `(type, name [, feat [, mask]])`, where:

*type* is the algorithm type as string, e.g. `aead`, `hash`,
`skcipher` or `rng`.
*name* is the algorithm name and operation mode as string, e.g.
`sha256`, `hmac(sha256)`, `cbc(aes)` or `drbg_nopr_ctr_aes256`.
*feat* and *mask* are unsigned 32bit integers.

[Availability](intro.html#availability): Linux >= 2.6.38.

Some algorithm types require more recent Kernels.

Added in version 3.6.

[`AF_VSOCK`](#socket.AF_VSOCK) allows communication between virtual machines and
their hosts. The sockets are represented as a `(CID, port)` tuple
where the context ID or CID and port are integers.

[Availability](intro.html#availability): Linux >= 3.9

See *[vsock(7)](https://manpages.debian.org/vsock(7))*

Added in version 3.7.

[`AF_PACKET`](#socket.AF_PACKET) is a low-level interface directly to network devices.
The addresses are represented by the tuple
`(ifname, proto[, pkttype[, hatype[, addr]]])` where:

*ifname* - String specifying the device name.

*proto* - The Ethernet protocol number.
May be [`ETH_P_ALL`](#socket.ETH_P_ALL) to capture all protocols,
one of the [ETHERTYPE_* constants](#socket-ethernet-types)
or any other Ethernet protocol number.
*pkttype* - Optional integer specifying the packet type:

`PACKET_HOST` (the default) - Packet addressed to the local host.

`PACKET_BROADCAST` - Physical-layer broadcast packet.

`PACKET_MULTICAST` - Packet sent to a physical-layer multicast address.

`PACKET_OTHERHOST` - Packet to some other host that has been caught by
a device driver in promiscuous mode.
`PACKET_OUTGOING` - Packet originating from the local host that is
looped back to a packet socket.

*hatype* - Optional integer specifying the ARP hardware address type.

*addr* - Optional bytes-like object specifying the hardware physical
address, whose interpretation depends on the device.

[Availability](intro.html#availability): Linux >= 2.2.

[`AF_QIPCRTR`](#socket.AF_QIPCRTR) is a Linux-only socket based interface for communicating
with services running on co-processors in Qualcomm platforms. The address
family is represented as a `(node, port)` tuple where the *node* and *port*
are non-negative integers.

[Availability](intro.html#availability): Linux >= 4.7.

Added in version 3.8.

`IPPROTO_UDPLITE` is a variant of UDP which allows you to specify
what portion of a packet is covered with the checksum. It adds two socket
options that you can change.
`self.setsockopt(IPPROTO_UDPLITE, UDPLITE_SEND_CSCOV, length)` will
change what portion of outgoing packets are covered by the checksum and
`self.setsockopt(IPPROTO_UDPLITE, UDPLITE_RECV_CSCOV, length)` will
filter out packets which cover too little of their data. In both cases
`length` should be in `range(8, 2**16, 8)`.
Such a socket should be constructed with
`socket(AF_INET, SOCK_DGRAM, IPPROTO_UDPLITE)` for IPv4 or
`socket(AF_INET6, SOCK_DGRAM, IPPROTO_UDPLITE)` for IPv6.

[Availability](intro.html#availability): Linux >= 2.6.20, FreeBSD >= 10.1

Added in version 3.9.

[`AF_HYPERV`](#socket.AF_HYPERV) is a Windows-only socket based interface for communicating
with Hyper-V hosts and guests. The address family is represented as a
`(vm_id, service_id)` tuple where the `vm_id` and `service_id` are
UUID strings.
The `vm_id` is the virtual machine identifier or a set of known VMID values
if the target is not a specific virtual machine. Known VMID constants
defined on `socket` are:

`HV_GUID_ZERO`

`HV_GUID_BROADCAST`

`HV_GUID_WILDCARD` - Used to bind on itself and accept connections from
all partitions.
`HV_GUID_CHILDREN` - Used to bind on itself and accept connection from
child partitions.
`HV_GUID_LOOPBACK` - Used as a target to itself.

`HV_GUID_PARENT` - When used as a bind accepts connection from the parent
partition. When used as an address target it will connect to the parent partition.

The `service_id` is the service identifier of the registered service.

Added in version 3.12.

If you use a hostname in the *host* portion of IPv4/v6 socket address, the
program may show a nondeterministic behavior, as Python uses the first address
returned from the DNS resolution.  The socket address will be resolved
differently into an actual IPv4/v6 address, depending on the results from DNS
resolution and/or the host configuration.  For deterministic behavior use a
numeric address in *host* portion.
All errors raise exceptions.  The normal exceptions for invalid argument types
and out-of-memory conditions can be raised. Errors
related to socket or address semantics raise [`OSError`](exceptions.html#OSError) or one of its
subclasses.
Non-blocking mode is supported through [`setblocking()`](#socket.socket.setblocking).  A
generalization of this based on timeouts is supported through
[`settimeout()`](#socket.socket.settimeout).

## Module contents[¶](#module-contents)

The module `socket` exports the following elements.

### Exceptions[¶](#exceptions)

*exception *socket.error[¶](#socket.error)
A deprecated alias of [`OSError`](exceptions.html#OSError).

Changed in version 3.3: Following [**PEP 3151**](https://peps.python.org/pep-3151/), this class was made an alias of [`OSError`](exceptions.html#OSError).

*exception *socket.herror[¶](#socket.herror)
A subclass of [`OSError`](exceptions.html#OSError), this exception is raised for
address-related errors, i.e. for functions that use *h_errno* in the POSIX


... [Content truncated]