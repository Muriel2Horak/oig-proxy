# Untitled

> Source: https://tldp.org/HOWTO/html_single/TCP-Keepalive-HOWTO/
> Cached: 2026-02-17T19:16:05.724Z

---

TCP Keepalive HOWTOTCP Keepalive HOWTOFabio Busatto&#60;fabio.busatto@sikurezza.org&#62;2007-05-04
Revision HistoryRevision 1.02007-05-04Revised by: FBFirst release, reviewed by TM.&#13;        This document describes the TCP keepalive implementation in the linux
        kernel, introduces the overall concept and points to both system
        configuration and software development.
      Table of Contents1. Introduction1.1. Copyright and License1.2. Disclaimer1.3. Credits / Contributors1.4. Feedback1.5. Translations2. TCP keepalive overview2.1. What is TCP keepalive?2.2. Why use TCP keepalive?2.3. Checking for dead peers2.4. Preventing disconnection due to network inactivity3. Using TCP keepalive under Linux3.1. Configuring the kernel3.2. Making changes persistent to reboot4. Programming applications4.1. When your code needs keepalive support4.2. The setsockopt function call4.3. Code examples5. Adding support to third-party software5.1. Modifying source code5.2. libkeepalive: library preloading1. Introduction&#13;    Understanding TCP keepalive is not necessary in most cases, but it's a
    subject that can be very useful under particular circumstances. You will
    need to know basic TCP/IP networking concepts, and the C programming
    language to understand all sections of this document.
  &#13;    The main purpose of this HOWTO is to describe TCP keepalive in detail and
    demonstrate various application situations. After some initial theory, the
    discussion focuses on the Linux implementation of TCP keepalive routines in
    the modern Linux kernel releases (2.4.x, 2.6.x), and how system
    administrators can take advantage of these routines, with specific
    configuration examples and tricks.
  &#13;    The second part of the HOWTO involves the programming interface exposed by
    the Linux kernel, and how to write TCP keepalive-enabled applications in the
    C language. Pratical examples are presented, and there is an introduction to
    the libkeepalive project, which permits legacy
    applications to benefit from keepalive with no code modification.
  1.1. Copyright and License&#13;      This document, TCP Keepalive HOWTO, is copyrighted (c) 2007 by Fabio
      Busatto. Permission is granted to copy, distribute and/or modify this
      document under the terms of the GNU Free Documentation License, Version
      1.1 or any later version published by the Free Software Foundation; with
      no Invariant Sections, with no Front-Cover Texts, and with no Back-Cover
      Texts. A copy of the license is available at
      &#13;      http://www.gnu.org/copyleft/fdl.html.
    &#13;      Source code included in this document is released under the terms of the
      GNU General Public License, Version 2 or any later version published by
      the Free Software Foundation. A copy of the license is available at
      &#13;      http://www.gnu.org/copyleft/gpl.html.
    &#13;      Linux is a registered trademark of Linus Torvalds.
    1.2. Disclaimer&#13;      No liability for the contents of this document can be accepted. Use the
      concepts, examples and information at your own risk. There may be errors
      and inaccuracies that could be damaging to your system. Proceed with
      caution, and although this is highly unlikely, the author does not take
      any responsibility.
    &#13;      All copyrights are held by their by their respective owners, unless
      specifically noted otherwise. Use of a term in this document should not be
      regarded as affecting the validity of any trademark or service mark.
      Naming of particular products or brands should not be seen as
      endorsements.
    1.3. Credits / Contributors&#13;      This work is not especially related to any people that I should thank. But
      my life is, and my knowledge too: so, thanks to everyone that has
      supported me, prior to my birth, now, and in the future. Really.
    &#13;      A special thank is due to Tabatha, the patient woman that read my work and
      made the needed reviews.
    1.4. Feedback&#13;      Feedback is most certainly welcome for this document. Send your additions,
      comments and criticisms to the following email address:
      &#60;fabio.busatto@sikurezza.org&#62;.
    1.5. Translations&#13;      There are no translated versions of this HOWTO at the time of publication.
      If you are interested in translating this HOWTO into other languages,
      please feel free to contact me. Your contribution will be very welcome.
    2. TCP keepalive overview&#13;    In order to understand what TCP keepalive (which we will just call
    keepalive) does, you need do nothing more than read the name: keep TCP
    alive. This means that you will be able to check your connected socket (also
    known as TCP sockets), and determine whether the connection is still up and
    running or if it has broken.
  2.1. What is TCP keepalive?&#13;      The keepalive concept is very simple: when you set up a TCP connection,
      you associate a set of timers. Some of these timers deal with the
      keepalive procedure. When the keepalive timer reaches zero, you send your
      peer a keepalive probe packet with no data in it and the ACK flag turned
      on. You can do this because of the TCP/IP specifications, as a sort of
      duplicate ACK, and the remote endpoint will have no arguments, as TCP is a
      stream-oriented protocol. On the other hand, you will receive a reply from
      the remote host (which doesn't need to support keepalive at all, just
      TCP/IP), with no data and the ACK set.
    &#13;      If you receive a reply to your keepalive probe, you can assert that the
      connection is still up and running without worrying about the user-level
      implementation. In fact, TCP permits you to handle a stream, not packets,
      and so a zero-length data packet is not dangerous for the user program.
    &#13;      This procedure is useful because if the other peers lose their connection
      (for example by rebooting) you will notice that the connection is broken,
      even if you don't have traffic on it. If the keepalive probes are not
      replied to by your peer, you can assert that the connection cannot be
      considered valid and then take the correct action.
    2.2. Why use TCP keepalive?&#13;      You can live quite happily without keepalive, so if you're reading this,
      you may be trying to understand if keepalive is a possible solution for
      your problems. Either that or you've really got nothing more interesting
      to do instead, and that's okay too. :)
    &#13;      Keepalive is non-invasive, and in most cases, if you're in doubt, you can
      turn it on without the risk of doing something wrong. But do remember that
      it generates extra network traffic, which can have an impact on routers
      and firewalls.
    &#13;      In short, use your brain and be careful.
    &#13;      In the next section we will distinguish between the two target tasks for
      keepalive:
      
Checking for dead peersPreventing disconnection due to network inactivity
    2.3. Checking for dead peers&#13;      Keepalive can be used to advise you when your peer dies before it is able
      to notify you. This could happen for several reasons, like kernel panic or
      a brutal termination of the process handling that peer. Another scenario
      that illustrates when you need keepalive to detect peer death is when the
      peer is still alive but the network channel between it and you has gone
      down. In this scenario, if the network doesn't become operational again,
      you have the equivalent of peer death. This is one of those situations
      where normal TCP operations aren't useful to check the connection status.
    &#13;      Think of a simple TCP connection between Peer A and Peer B: there is the
      initial three-way handshake, with one SYN segment from A to B, the SYN/ACK
      back from B to A, and the final ACK from A to B. At this time, we're in a
      stable status: connection is established, and now we would normally wait
      for someone to send data over the channel. And here comes the problem:
      unplug the power supply from B and instantaneously it will go down,
      without sending anything over the network to notify A that the connection
      is going to be broken. A, from its side, is ready to receive data, and has
      no idea that B has crashed. Now restore the power supply to B and wait for
      the system to restart. A and B are now back again, but while A knows about
      a connection still active with B, B has no idea. The situation resolves
      itself when A tries to send data to B over the dead connection, and B
      replies with an RST packet, causing A to finally to close the connection.
    &#13;      Keepalive can tell you when another peer becomes unreachable without the
      risk of false-positives. In fact, if the problem is in the network between
      two peers, the keepalive action is to wait some time and then retry,
      sending the keepalive packet before marking the connection as broken.
    &#13;      &#13;    _____                                                     _____
   |     |                                                   |     |
   |  A  |                                                   |  B  |
   |_____|                                                   |_____|
      ^                                                         ^
      |---&#62;---&#62;---&#62;-------------- SYN --------------&#62;---&#62;---&#62;---|
      |---&#60;---&#60;---&#60;------------ SYN/ACK ------------&#60;---&#60;---&#60;---|
      |---&#62;---&#62;---&#62;-------------- ACK --------------&#62;---&#62;---&#62;---|
      |                                                         |
      |                                       system crash ---&#62; X
      |
      |                                     system restart ---&#62; ^
      |                                                         |
      |---&#62;---&#62;---&#62;-------------- PSH --------------&#62;---&#62;---&#62;---|
      |---&#60;---&#60;---&#60;-------------- RST --------------&#60;---&#60;---&#60;---|
      |                                                         |

      
    2.4. Preventing disconnection due to network inactivity&#13;      The other useful goal of keepalive is to prevent inactivity from
      disconnecting the channel. It's a very common issue, when you are behind a
      NAT proxy or a firewall, to be disconnected without a reason. This
      behavior is caused by the connection tracking procedures implemented in
      proxies and firewalls, which keep track of all connections that pass
      through them. Because of the physical limits of these machines, they can
      only keep a finite number of connections in their memory. The most common
      and logical policy is to keep newest connections and to discard old and
      inactive connections first.
    &#13;      Returning to Peers A and B, reconnect them. Once the channel is open, wait
      until an event occurs and then communicate this to the other peer. What if
      the event verifies after a long period of time? Our connection has its
      scope, but it's unknown to the proxy. So when we finally send data, the
      proxy isn't able to correctly handle it, and the connection breaks up.
    &#13;      Because the normal implementation puts the connection at the top of the
      list when one of its packets arrives and selects the last connection in
      the queue when it needs to eliminate an entry, periodically sending
      packets over the network is a good way to always be in a polar position
      with a minor risk of deletion.
    &#13;      &#13;    _____           _____                                     _____
   |     |         |     |                                   |     |
   |  A  |         | NAT |                                   |  B  |
   |_____|         |_____|                                   |_____|
      ^               ^                                         ^
      |---&#62;---&#62;---&#62;---|----------- SYN -------------&#62;---&#62;---&#62;---|
      |---&#60;---&#60;---&#60;---|--------- SYN/ACK -----------&#60;---&#60;---&#60;---|
      |---&#62;---&#62;---&#62;---|----------- ACK -------------&#62;---&#62;---&#62;---|
      |               |                                         |
      |               | &#60;--- connection deleted from table      |
      |               |                                         |
      |---&#62;- PSH -&#62;---| &#60;--- invalid connection                 |
      |               |                                         |

      
    3. Using TCP keepalive under Linux&#13;    Linux has built-in support for keepalive. You need to enable TCP/IP
    networking in order to use it. You also need procfs
    support and sysctl support to be able to configure the
    kernel parameters at runtime.
  &#13;    The procedures involving keepalive use three user-driven variables:

    tcp_keepalive_time&#13;            the interval between the last data packet sent (simple ACKs are not
            considered data) and the first keepalive probe; after the connection
            is marked to need keepalive, this counter is not used any further
          tcp_keepalive_intvl&#13;            the interval between subsequential keepalive probes, regardless of
            what the connection has exchanged in the meantime
          tcp_keepalive_probes&#13;            the number of unacknowledged probes to send before considering the
            connection dead and notifying the application layer
          
  &#13;    Remember that keepalive support, even if configured in the kernel, is not
    the default behavior in Linux. Programs must request keepalive control for
    their sockets using the setsockopt interface. There are
    relatively few programs implementing keepalive, but you can easily add
    keepalive support for most of them following the instructions explained
    later in this document.
  3.1. Configuring the kernel&#13;      There are two ways to configure keepalive parameters inside the kernel via
      userspace commands:

      
procfs interfacesysctl interface
    &#13;      We mainly discuss how this is accomplished on the procfs interface because
      it's the most used, recommended and the easiest to understand. The sysctl
      interface, particularly regarding the &#13;      sysctl(2) syscall and not the &#13;      sysctl(8)
      tool, is only here for the purpose of background knowledge.
    3.1.1. The procfs interface&#13;        This interface requires both sysctl and &#13;        procfs to be built into the kernel, and procfs
         mounted somewhere in the filesystem (usually on &#13;        /proc, as in the examples below). You can read the values for
        the actual parameters by "cattin

... [Content truncated]