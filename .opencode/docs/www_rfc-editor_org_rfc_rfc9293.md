# RFC 9293: Transmission Control Protocol (TCP)

> Source: https://www.rfc-editor.org/rfc/rfc9293
> Cached: 2026-02-18T03:56:44.888Z

---

RFC 9293
TCP
August 2022

Eddy
Standards Track
[Page]

Stream:
Internet Engineering Task Force (IETF)
STD:
7
RFC:
[9293](https://www.rfc-editor.org/rfc/rfc9293)
Obsoletes:

[793](https://www.rfc-editor.org/rfc/rfc793), [879](https://www.rfc-editor.org/rfc/rfc879), [2873](https://www.rfc-editor.org/rfc/rfc2873), [6093](https://www.rfc-editor.org/rfc/rfc6093), [6429](https://www.rfc-editor.org/rfc/rfc6429), [6528](https://www.rfc-editor.org/rfc/rfc6528), [6691](https://www.rfc-editor.org/rfc/rfc6691) 
Updates:

[1011](https://www.rfc-editor.org/rfc/rfc1011), [1122](https://www.rfc-editor.org/rfc/rfc1122), [5961](https://www.rfc-editor.org/rfc/rfc5961) 
Category:
Standards Track
Published:

August 2022
    
ISSN:
2070-1721
Author:

      W. Eddy, Ed.

MTI Systems

# RFC 9293

# Transmission Control Protocol (TCP)

      ## [Abstract](#abstract)

This document specifies the Transmission Control Protocol (TCP).  TCP is an important transport-layer protocol in the Internet protocol stack, and it has continuously evolved over decades of use and growth of the Internet.  Over this time, a number of changes have been made to TCP as it was specified in RFC 793, though these have only been documented in a piecemeal fashion.  This document collects and brings those changes together with the protocol specification from RFC 793.  This document obsoletes RFC 793, as well as RFCs 879, 2873, 6093, 6429, 6528, and 6691 that updated parts of RFC 793.  It updates RFCs 1011 and 1122, and it should be considered as a replacement for the portions of those documents dealing with TCP requirements.  It also updates RFC 5961 by adding a small clarification in reset handling while in the SYN-RECEIVED state.  The TCP header control bits from RFC 793 have also been updated based on RFC 3168.[¶](#section-abstract-1)

        
[Status of This Memo](#name-status-of-this-memo)
        

            This is an Internet Standards Track document.[¶](#section-boilerplate.1-1)

            This document is a product of the Internet Engineering Task Force
            (IETF).  It represents the consensus of the IETF community.  It has
            received public review and has been approved for publication by
            the Internet Engineering Steering Group (IESG).  Further
            information on Internet Standards is available in Section 2 of 
            RFC 7841.[¶](#section-boilerplate.1-2)

            Information about the current status of this document, any
            errata, and how to provide feedback on it may be obtained at
            [https://www.rfc-editor.org/info/rfc9293](https://www.rfc-editor.org/info/rfc9293).[¶](#section-boilerplate.1-3)

        
[Copyright Notice](#name-copyright-notice)
        

            Copyright (c) 2022 IETF Trust and the persons identified as the
            document authors. All rights reserved.[¶](#section-boilerplate.2-1)

            This document is subject to BCP 78 and the IETF Trust's Legal
            Provisions Relating to IETF Documents
            ([https://trustee.ietf.org/license-info](https://trustee.ietf.org/license-info)) in effect on the date of
            publication of this document. Please review these documents
            carefully, as they describe your rights and restrictions with
            respect to this document. Code Components extracted from this
            document must include Revised BSD License text as described in
            Section 4.e of the Trust Legal Provisions and are provided without
            warranty as described in the Revised BSD License.[¶](#section-boilerplate.2-2)

            This document may contain material from IETF Documents or IETF
            Contributions published or made publicly available before November
            10, 2008. The person(s) controlling the copyright in some of this
            material may not have granted the IETF Trust the right to allow
            modifications of such material outside the IETF Standards Process.
            Without obtaining an adequate license from the person(s)
            controlling the copyright in such materials, this document may not
            be modified outside the IETF Standards Process, and derivative
            works of it may not be created outside the IETF Standards Process,
            except to format it for publication as an RFC or to translate it
            into languages other than English.[¶](#section-boilerplate.2-3)

        [▲](#)
[Table of Contents](#name-table-of-contents)
        

            [1](#section-1).  [Purpose and Scope](#name-purpose-and-scope)

          
            [2](#section-2).  [Introduction](#name-introduction)

                [2.1](#section-2.1).  [Requirements Language](#name-requirements-language)

              
                [2.2](#section-2.2).  [Key TCP Concepts](#name-key-tcp-concepts)

            

          
            [3](#section-3).  [Functional Specification](#name-functional-specification)

                [3.1](#section-3.1).  [Header Format](#name-header-format)

              
                [3.2](#section-3.2).  [Specific Option Definitions](#name-specific-option-definitions)

                    [3.2.1](#section-3.2.1).  [Other Common Options](#name-other-common-options)

                  
                    [3.2.2](#section-3.2.2).  [Experimental TCP Options](#name-experimental-tcp-options)

                

              
                [3.3](#section-3.3).  [TCP Terminology Overview](#name-tcp-terminology-overview)

                    [3.3.1](#section-3.3.1).  [Key Connection State Variables](#name-key-connection-state-variab)

                  
                    [3.3.2](#section-3.3.2).  [State Machine Overview](#name-state-machine-overview)

                

              
                [3.4](#section-3.4).  [Sequence Numbers](#name-sequence-numbers)

                    [3.4.1](#section-3.4.1).  [Initial Sequence Number Selection](#name-initial-sequence-number-sel)

                  
                    [3.4.2](#section-3.4.2).  [Knowing When to Keep Quiet](#name-knowing-when-to-keep-quiet)

                  
                    [3.4.3](#section-3.4.3).  [The TCP Quiet Time Concept](#name-the-tcp-quiet-time-concept)

                

              
                [3.5](#section-3.5).  [Establishing a Connection](#name-establishing-a-connection)

                    [3.5.1](#section-3.5.1).  [Half-Open Connections and Other Anomalies](#name-half-open-connections-and-o)

                  
                    [3.5.2](#section-3.5.2).  [Reset Generation](#name-reset-generation)

                  
                    [3.5.3](#section-3.5.3).  [Reset Processing](#name-reset-processing)

                

              
                [3.6](#section-3.6).  [Closing a Connection](#name-closing-a-connection)

                    [3.6.1](#section-3.6.1).  [Half-Closed Connections](#name-half-closed-connections)

                

              
                [3.7](#section-3.7).  [Segmentation](#name-segmentation)

                    [3.7.1](#section-3.7.1).  [Maximum Segment Size Option](#name-maximum-segment-size-option)

                  
                    [3.7.2](#section-3.7.2).  [Path MTU Discovery](#name-path-mtu-discovery)

                  
                    [3.7.3](#section-3.7.3).  [Interfaces with Variable MTU Values](#name-interfaces-with-variable-mt)

                  
                    [3.7.4](#section-3.7.4).  [Nagle Algorithm](#name-nagle-algorithm)

                  
                    [3.7.5](#section-3.7.5).  [IPv6 Jumbograms](#name-ipv6-jumbograms)

                

              
                [3.8](#section-3.8).  [Data Communication](#name-data-communication)

                    [3.8.1](#section-3.8.1).  [Retransmission Timeout](#name-retransmission-timeout)

                  
                    [3.8.2](#section-3.8.2).  [TCP Congestion Control](#name-tcp-congestion-control)

                  
                    [3.8.3](#section-3.8.3).  [TCP Connection Failures](#name-tcp-connection-failures)

                  
                    [3.8.4](#section-3.8.4).  [TCP Keep-Alives](#name-tcp-keep-alives)

                  
                    [3.8.5](#section-3.8.5).  [The Communication of Urgent Information](#name-the-communication-of-urgent)

                  
                    [3.8.6](#section-3.8.6).  [Managing the Window](#name-managing-the-window)

                

              
                [3.9](#section-3.9).  [Interfaces](#name-interfaces)

                    [3.9.1](#section-3.9.1).  [User/TCP Interface](#name-user-tcp-interface)

                  
                    [3.9.2](#section-3.9.2).  [TCP/Lower-Level Interface](#name-tcp-lower-level-interface)

                

              
                [3.10](#section-3.10). [Event Processing](#name-event-processing)

                    [3.10.1](#section-3.10.1).  [OPEN Call](#name-open-call)

                  
                    [3.10.2](#section-3.10.2).  [SEND Call](#name-send-call)

                  
                    [3.10.3](#section-3.10.3).  [RECEIVE Call](#name-receive-call)

                  
                    [3.10.4](#section-3.10.4).  [CLOSE Call](#name-close-call)

                  
                    [3.10.5](#section-3.10.5).  [ABORT Call](#name-abort-call)

                  
                    [3.10.6](#section-3.10.6).  [STATUS Call](#name-status-call)

                  
                    [3.10.7](#section-3.10.7).  [SEGMENT ARRIVES](#name-segment-arrives)

                  
                    [3.10.8](#section-3.10.8).  [Timeouts](#name-timeouts)

                

            

          
            [4](#section-4).  [Glossary](#name-glossary)

          
            [5](#section-5).  [Changes from RFC 793](#name-changes-from-rfc-793)

          
            [6](#section-6).  [IANA Considerations](#name-iana-considerations)

          
            [7](#section-7).  [Security and Privacy Considerations](#name-security-and-privacy-consid)

          
            [8](#section-8).  [References](#name-references)

                [8.1](#section-8.1).  [Normative References](#name-normative-references)

              
                [8.2](#section-8.2).  [Informative References](#name-informative-references)

            

          
            [Appendix A](#appendix-A).  [Other Implementation Notes](#name-other-implementation-notes)

                [A.1](#appendix-A.1).  [IP Security Compartment and Precedence](#name-ip-security-compartment-and)

                    [A.1.1](#appendix-A.1.1).  [Precedence](#name-precedence)

                  
                    [A.1.2](#appendix-A.1.2).  [MLS Systems](#name-mls-systems)

                

              
                [A.2](#appendix-A.2).  [Sequence Number Validation](#name-sequence-number-validation)

              
                [A.3](#appendix-A.3).  [Nagle Modification](#name-nagle-modification)

              
                [A.4](#appendix-A.4).  [Low Watermark Settings](#name-low-watermark-settings)

            

          
            [Appendix B](#appendix-B).  [TCP Requirement Summary](#name-tcp-requirement-summary)

          
            [](#appendix-C)[Acknowledgments](#name-acknowledgments)

          
            [](#appendix-D)[Author's Address](#name-authors-address)

        

      
[1. ](#section-1)[Purpose and Scope](#name-purpose-and-scope)
      

        In 1981, [RFC 793](#RFC0793) [[16](#RFC0793)] was released, documenting the Transmission Control Protocol (TCP) and replacing earlier published specifications for TCP.[¶](#section-1-1)

        Since then, TCP has been widely implemented, and it has been used as a transport protocol for numerous applications on the Internet.[¶](#section-1-2)

        For several decades, RFC 793 plus a number of other documents have combined to serve as the core specification for TCP [[49](#RFC7414)].  Over time, a number of errata have been filed against RFC 793.  There have also been deficiencies found and resolved in security, performance, and many other aspects.  The number of enhancements has grown over time across many separate documents.  These were never accumulated together into a comprehensive update to the base specification.[¶](#section-1-3)

        The purpose of this document is to bring together all of the IETF Standards Track changes and other clarifications that have been made to the base TCP functional specification (RFC 793) and to unify them into an updated version of the specification.[¶](#section-1-4)

 Some companion documents are referenced for important algorithms that are used by TCP (e.g., for congestion control) but have not been completely included in this document.  This is a conscious choice, as this base specification can be used with multiple additional algorithms that are developed and incorporated separately. This document focuses on the common basis that all TCP implementations must support in order to interoperate.  Since some additional TCP features have become quite complicated themselves (e.g., advanced loss recovery and congestion control), future companion documents may attempt to similarly bring these together.[¶](#section-1-5)

        In addition to the protocol specification that describes the TCP segment format, generation, and processing rules that are to be implemented in code, RFC 793 and other updates also contain informative and descriptive text for readers to understand aspects of the protocol design and operation.  This document does not attempt to alter or update this informative text and is focused only on updating the normative protocol specification.  This document preserves references to the documentation containing the important explanations and rationale, where appropriate.[¶](#section-1-6)

        This document is intended to be useful both in checking existing TCP implementations for conformance purposes, as well as in writing new implementations.[¶](#section-1-7)

      
[2. ](#section-2)[Introduction](#name-introduction)
      
RFC 793 contains a discussion of the TCP design goals and provides examples of its operation, including examples of connection establishment, connection termination, and packet retransmission to repair losses.[¶](#section-2-1)

        This document describes the basic functionality expected in modern TCP implementations and replaces the protocol specification in RFC 793.  It does not replicate or attempt to update the introduction and philosophy content in Sections 1 and 2 of RFC 793.  Other documents are referenced to provide explanations of the theory of operation, rationale, and detailed discussion of design decisions.  This document only focuses on the normative behavior of the protocol.[¶](#section-2-2)

 The "TCP Roadmap" [[49](#RFC7414)] provides a more extensive guide to the RFCs that define TCP and describe various important algorithms. The TCP

... [Content truncated]