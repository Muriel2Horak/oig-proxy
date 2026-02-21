# 4. Reliable Request-Reply Patterns | ØMQ - The Guide

> Source: https://zguide.zeromq.org/docs/chapter4/
> Cached: 2026-02-17T19:16:06.716Z

---

Chapter 4 - Reliable Request-Reply Patterns
  [#](#reliable-request-reply)

  [Chapter 3 - Advanced Request-Reply Patterns](/docs/chapter3/#advanced-request-reply) covered advanced uses of ZeroMQ&rsquo;s request-reply pattern with working examples. This chapter looks at the general question of reliability and builds a set of reliable messaging patterns on top of ZeroMQ&rsquo;s core request-reply pattern.
In this chapter, we focus heavily on user-space request-reply *patterns*, reusable models that help you design your own ZeroMQ architectures:

- The *Lazy Pirate* pattern: reliable request-reply from the client side

- The *Simple Pirate* pattern: reliable request-reply using load balancing

- The *Paranoid Pirate* pattern: reliable request-reply with heartbeating

- The *Majordomo* pattern: service-oriented reliable queuing

- The *Titanic* pattern: disk-based/disconnected reliable queuing

- The *Binary Star* pattern: primary-backup server failover

- The *Freelance* pattern: brokerless reliable request-reply

  What is &ldquo;Reliability&rdquo;?
  [#](#What-is-Reliability)

Most people who speak of &ldquo;reliability&rdquo; don&rsquo;t really know what they mean. We can only define reliability in terms of failure. That is, if we can handle a certain set of well-defined and understood failures, then we are reliable with respect to those failures. No more, no less. So let&rsquo;s look at the possible causes of failure in a distributed ZeroMQ application, in roughly descending order of probability:

Application code is the worst offender. It can crash and exit, freeze and stop responding to input, run too slowly for its input, exhaust all memory, and so on.

System code&ndash;such as brokers we write using ZeroMQ&ndash;can die for the same reasons as application code. System code *should* be more reliable than application code, but it can still crash and burn, and especially run out of memory if it tries to queue messages for slow clients.

Message queues can overflow, typically in system code that has learned to deal brutally with slow clients. When a queue overflows, it starts to discard messages. So we get &ldquo;lost&rdquo; messages.

Networks can fail (e.g., WiFi gets switched off or goes out of range). ZeroMQ will automatically reconnect in such cases, but in the meantime, messages may get lost.

Hardware can fail and take with it all the processes running on that box.

Networks can fail in exotic ways, e.g., some ports on a switch may die and those parts of the network become inaccessible.

Entire data centers can be struck by lightning, earthquakes, fire, or more mundane power or cooling failures.

To make a software system fully reliable against *all* of these possible failures is an enormously difficult and expensive job and goes beyond the scope of this book.

Because the first five cases in the above list cover 99.9% of real world requirements outside large companies (according to a highly scientific study I just ran, which also told me that 78% of statistics are made up on the spot, and moreover never to trust a statistic that we didn&rsquo;t falsify ourselves), that&rsquo;s what we&rsquo;ll examine. If you&rsquo;re a large company with money to spend on the last two cases, contact my company immediately! There&rsquo;s a large hole behind my beach house waiting to be converted into an executive swimming pool.

  Designing Reliability
  [#](#Designing-Reliability)

So to make things brutally simple, reliability is &ldquo;keeping things working properly when code freezes or crashes&rdquo;, a situation we&rsquo;ll shorten to &ldquo;dies&rdquo;. However, the things we want to keep working properly are more complex than just messages. We need to take each core ZeroMQ messaging pattern and see how to make it work (if we can) even when code dies.

Let&rsquo;s take them one-by-one:

Request-reply: if the server dies (while processing a request), the client can figure that out because it won&rsquo;t get an answer back. Then it can give up in a huff, wait and try again later, find another server, and so on. As for the client dying, we can brush that off as &ldquo;someone else&rsquo;s problem&rdquo; for now.

Pub-sub: if the client dies (having gotten some data), the server doesn&rsquo;t know about it. Pub-sub doesn&rsquo;t send any information back from client to server. But the client can contact the server out-of-band, e.g., via request-reply, and ask, &ldquo;please resend everything I missed&rdquo;. As for the server dying, that&rsquo;s out of scope for here. Subscribers can also self-verify that they&rsquo;re not running too slowly, and take action (e.g., warn the operator and die) if they are.

Pipeline: if a worker dies (while working), the ventilator doesn&rsquo;t know about it. Pipelines, like the grinding gears of time, only work in one direction. But the downstream collector can detect that one task didn&rsquo;t get done, and send a message back to the ventilator saying, &ldquo;hey, resend task 324!&rdquo; If the ventilator or collector dies, whatever upstream client originally sent the work batch can get tired of waiting and resend the whole lot. It&rsquo;s not elegant, but system code should really not die often enough to matter.

In this chapter we&rsquo;ll focus just on request-reply, which is the low-hanging fruit of reliable messaging.

The basic request-reply pattern (a REQ client socket doing a blocking send/receive to a REP server socket) scores low on handling the most common types of failure. If the server crashes while processing the request, the client just hangs forever. If the network loses the request or the reply, the client hangs forever.

Request-reply is still much better than TCP, thanks to ZeroMQ&rsquo;s ability to reconnect peers silently, to load balance messages, and so on. But it&rsquo;s still not good enough for real work. The only case where you can really trust the basic request-reply pattern is between two threads in the same process where there&rsquo;s no network or separate server process to die.

However, with a little extra work, this humble pattern becomes a good basis for real work across a distributed network, and we get a set of reliable request-reply (RRR) patterns that I like to call the *Pirate* patterns (you&rsquo;ll eventually get the joke, I hope).

There are, in my experience, roughly three ways to connect clients to servers. Each needs a specific approach to reliability:

Multiple clients talking directly to a single server. Use case: a single well-known server to which clients need to talk. Types of failure we aim to handle: server crashes and restarts, and network disconnects.

Multiple clients talking to a broker proxy that distributes work to multiple workers. Use case: service-oriented transaction processing. Types of failure we aim to handle: worker crashes and restarts, worker busy looping, worker overload, queue crashes and restarts, and network disconnects.

Multiple clients talking to multiple servers with no intermediary proxies. Use case: distributed services such as name resolution. Types of failure we aim to handle: service crashes and restarts, service busy looping, service overload, and network disconnects.

Each of these approaches has its trade-offs and often you&rsquo;ll mix them. We&rsquo;ll look at all three in detail.

  Client-Side Reliability (Lazy Pirate Pattern)
  [#](#Client-Side-Reliability-Lazy-Pirate-Pattern)

We can get very simple reliable request-reply with some changes to the client. We call this the Lazy Pirate pattern. Rather than doing a blocking receive, we:

- Poll the REQ socket and receive from it only when it&rsquo;s sure a reply has arrived.

- Resend a request, if no reply has arrived within a timeout period.

- Abandon the transaction if there is still no reply after several requests.

If you try to use a REQ socket in anything other than a strict send/receive fashion, you&rsquo;ll get an error (technically, the REQ socket implements a small finite-state machine to enforce the send/receive ping-pong, and so the error code is called &ldquo;EFSM&rdquo;). This is slightly annoying when we want to use REQ in a pirate pattern, because we may send several requests before getting a reply.

The pretty good brute force solution is to close and reopen the REQ socket after an error:

  
  
  
  
  
    lpclient: Lazy Pirate client in Ada
      
      
        
          
              The example **lpclient** is missing in **Ada**:
              [Contribute Translation](/translate)
          
      
    
  

  
  
  
  
  
    lpclient: Lazy Pirate client in Basic
      
      
        
          
              The example **lpclient** is missing in **Basic**:
              [Contribute Translation](/translate)
          
      
    
  

  
  
  
  
  
    lpclient: Lazy Pirate client in C
      
      
        
          
            ```
#include <czmq.h>
#define REQUEST_TIMEOUT 2500 // msecs, (>1000!)
#define REQUEST_RETRIES 3 // Before we abandon
#define SERVER_ENDPOINT &#34;tcp://localhost:5555&#34;

int main()
{
    zsock_t *client = zsock_new_req(SERVER_ENDPOINT);
    printf(&#34;I: Connecting to server...\n&#34;);
    assert(client);

    int sequence = 0;
    int retries_left = REQUEST_RETRIES;
    printf(&#34;Entering while loop...\n&#34;);
    while(retries_left) // interrupt needs to be handled
    {
        // We send a request, then we get a reply
        char request[10];
        sprintf(request, &#34;%d&#34;, ++sequence);
        zstr_send(client, request);
        int expect_reply = 1;
        while(expect_reply)
        {
            printf(&#34;Expecting reply....\n&#34;);
            zmq_pollitem_t items [] = {{zsock_resolve(client), 0, ZMQ_POLLIN, 0}};
            printf(&#34;After polling\n&#34;);
            int rc = zmq_poll(items, 1, REQUEST_TIMEOUT * ZMQ_POLL_MSEC);
            printf(&#34;Polling Done.. \n&#34;);
            if (rc == -1)
                break; // Interrupted
            
            // Here we process a server reply and exit our loop if the
            // reply is valid. If we didn't get a reply we close the
            // client socket, open it again and resend the request. We
            // try a number times before finally abandoning:

            if (items[0].revents & ZMQ_POLLIN)
            {
                // We got a reply from the server, must match sequence
                char *reply = zstr_recv(client);
                if(!reply)
                    break; // interrupted
                if (atoi(reply) == sequence)
                {
                    printf(&#34;I: server replied OK (%s)\n&#34;, reply);
                    retries_left=REQUEST_RETRIES;
                    expect_reply = 0;
                }
                else
                {
                    printf(&#34;E: malformed reply from server: %s\n&#34;, reply);
                }
                free(reply);
            }
            else 
            {
                if(--retries_left == 0)
                {
                    printf(&#34;E: Server seems to be offline, abandoning\n&#34;);
                    break;
                }
                else
                {
                    printf(&#34;W: no response from server, retrying...\n&#34;);
                    zsock_destroy(&client);
                    printf(&#34;I: reconnecting to server...\n&#34;);
                    client = zsock_new_req(SERVER_ENDPOINT);
                    zstr_send(client, request);
                }
            }
        }
        zsock_destroy(&client);
        return 0;
    }
}
```

          
          
              
              
              Edit this example
            
          
      
    
  

  
  
  
  
  
    lpclient: Lazy Pirate client in C&#43;&#43;
      
      
        
          
            ```
//
//  Lazy Pirate client
//  Use zmq_poll to do a safe request-reply
//  To run, start piserver and then randomly kill/restart it
//
#include &#34;zhelpers.hpp&#34;

#include <sstream>

#define REQUEST_TIMEOUT     2500    //  msecs, (> 1000!)
#define REQUEST_RETRIES     3       //  Before we abandon

//  Helper function that returns a new configured socket
//  connected to the Hello World server
//
static zmq::socket_t * s_client_socket (zmq::context_t & context) {
    std::cout << &#34;I: connecting to server...&#34; << std::endl;
    zmq::socket_t * client = new zmq::socket_t (context, ZMQ_REQ);
    client->connect (&#34;tcp://localhost:5555&#34;);

    //  Configure socket to not wait at close time
    int linger = 0;
    client->setsockopt (ZMQ_LINGER, &linger, sizeof (linger));
    return client;
}

int main () {
    zmq::context_t context (1);

    zmq::socket_t * client = s_client_socket (context);

    int sequence = 0;
    int retries_left = REQUEST_RETRIES;

    while (retries_left) {
        std::stringstream request;
        request << ++sequence;
        s_send (*client, request.str());
        sleep (1);

        bool expect_reply = true;
        while (expect_reply) {
            //  Poll socket for a reply, with timeout
            zmq::pollitem_t items[] = {
                { *client, 0, ZMQ_POLLIN, 0 } };
            zmq::poll (&items[0], 1, REQUEST_TIMEOUT);

            //  If we got a reply, process it
            if (items[0].revents & ZMQ_POLLIN) {
                //  We got a reply from the server, must match sequence
                std::string reply = s_recv (*client);
                if (atoi (reply.c_str ()) == sequence) {
                    std::cout << &#34;I: server replied OK (&#34; << reply << &#34;)&#34; << std::endl;
                    retries_left = REQUEST_RETRIES;
                    expect_reply = false;
                }
                else {
                    std::cout << &#34;E: malformed reply from server: &#34; << reply << std::endl;
                }
            }
            else
            if (--retries_left == 0) {
                std::cout << &#34;E: server seems to be offline, abandoning&#34; << std::endl;
                expect_reply = false;
                break;
            }
            else {
                std::cout << &#34;W: no response from server, retrying...&#34; << std::endl;
                //  Old socket will be confused; close it and open a new one
                delete client;
                client = s_client_socket (context);
                //  Send request again, on new socket
                s_send (*client, request.str());
            }
        }
    }
    delete client;
    return 0;
}

```

          
          
              
              
              Edit this example
            
          
      
    
  

  
  
  
  
  
    lpclient: Lazy Pirate client in C#
      
      
        
          
            ```
﻿using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;

using ZeroMQ;

namespace Examples
{
	static partial class Program
	{
		//
		// Lazy Pirate

... [Content truncated]