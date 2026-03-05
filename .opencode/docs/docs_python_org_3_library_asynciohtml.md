# asyncio — Asynchronous I/O &#8212; Python 3.14.3 documentation

> Source: https://docs.python.org/3/library/asyncio.html
> Cached: 2026-02-17T19:30:30.316Z

---

Theme
    
        Auto
        Light
        Dark
    

  
    #### Previous topic

    [Networking and Interprocess Communication](ipc.html)
  
  
    #### Next topic

    [Runners](asyncio-runner.html)
  
  
    ### This page

    

      - [Report a bug](../bugs.html)

      
        Show source
        
      
      
    

  
        
    

  
    
      ### Navigation

      

        
          [index](../genindex.html)
        
          [modules](../py-modindex.html) |
        
          [next](asyncio-runner.html) |
        
          [previous](ipc.html) |

          - 

          - [Python](https://www.python.org/) &#187;

          
            
            
          
          
              
          
    
      [3.14.3 Documentation](../index.html) &#187;
    

          - [The Python Standard Library](index.html) &#187;

          - [Networking and Interprocess Communication](ipc.html) &#187;

        - [`asyncio` — Asynchronous I/O]()

                
                    

    
        
          
          
        
    
                     |
                
            

    Theme
    
        Auto
        Light
        Dark
    
 |
            
      

        

    
      
        
          
            
  
# `asyncio` — Asynchronous I/O[¶](#module-asyncio)

Hello World!

import asyncio

async def main():
    print('Hello ...')
    await asyncio.sleep(1)
    print('... World!')

asyncio.run(main())

asyncio is a library to write **concurrent** code using
the **async/await** syntax.
asyncio is used as a foundation for multiple Python asynchronous
frameworks that provide high-performance network and web-servers,
database connection libraries, distributed task queues, etc.
asyncio is often a perfect fit for IO-bound and high-level
**structured** network code.

See also

[A Conceptual Overview of asyncio](../howto/a-conceptual-overview-of-asyncio.html#a-conceptual-overview-of-asyncio)Explanation of the fundamentals of asyncio.

asyncio provides a set of **high-level** APIs to:

[run Python coroutines](asyncio-task.html#coroutine) concurrently and
have full control over their execution;
perform [network IO and IPC](asyncio-stream.html#asyncio-streams);

control [subprocesses](asyncio-subprocess.html#asyncio-subprocess);

distribute tasks via [queues](asyncio-queue.html#asyncio-queues);

[synchronize](asyncio-sync.html#asyncio-sync) concurrent code;

Additionally, there are **low-level** APIs for
*library and framework developers* to:

create and manage [event loops](asyncio-eventloop.html#asyncio-event-loop), which
provide asynchronous APIs for [networking](asyncio-eventloop.html#loop-create-server),
running [subprocesses](asyncio-eventloop.html#loop-subprocess-exec),
handling [OS signals](asyncio-eventloop.html#loop-add-signal-handler), etc;
implement efficient protocols using
[transports](asyncio-protocol.html#asyncio-transports-protocols);
[bridge](asyncio-future.html#asyncio-futures) callback-based libraries and code
with async/await syntax.

[Availability](intro.html#availability): not WASI.

This module does not work or is not available on WebAssembly. See
[WebAssembly platforms](intro.html#wasm-availability) for more information.

asyncio REPL

You can experiment with an `asyncio` concurrent context in the [REPL](../glossary.html#term-REPL):

$ python -m asyncio
asyncio REPL ...
Use "await" directly instead of "asyncio.run()".
Type "help", "copyright", "credits" or "license" for more information.
>>> import asyncio
>>> await asyncio.sleep(10, result='hello')
'hello'

This REPL provides limited compatibility with [`PYTHON_BASIC_REPL`](../using/cmdline.html#envvar-PYTHON_BASIC_REPL).
It is recommended that the default REPL is used
for full functionality and the latest features.
Raises an [auditing event](sys.html#auditing) `cpython.run_stdin` with no arguments.

Changed in version 3.12.5: (also 3.11.10, 3.10.15, 3.9.20, and 3.8.20)
Emits audit events.

Changed in version 3.13: Uses PyREPL if possible, in which case [`PYTHONSTARTUP`](../using/cmdline.html#envvar-PYTHONSTARTUP) is
also executed. Emits audit events.

Reference

High-level APIs

- [Runners](asyncio-runner.html)

- [Coroutines and Tasks](asyncio-task.html)

- [Streams](asyncio-stream.html)

- [Synchronization Primitives](asyncio-sync.html)

- [Subprocesses](asyncio-subprocess.html)

- [Queues](asyncio-queue.html)

- [Exceptions](asyncio-exceptions.html)

- [Call Graph Introspection](asyncio-graph.html)

Low-level APIs

- [Event Loop](asyncio-eventloop.html)

- [Futures](asyncio-future.html)

- [Transports and Protocols](asyncio-protocol.html)

- [Policies](asyncio-policy.html)

- [Platform Support](asyncio-platforms.html)

- [Extending](asyncio-extending.html)

Guides and Tutorials

- [High-level API Index](asyncio-api-index.html)

- [Low-level API Index](asyncio-llapi-index.html)

- [Developing with asyncio](asyncio-dev.html)

Note

The source code for asyncio can be found in [Lib/asyncio/](https://github.com/python/cpython/tree/3.14/Lib/asyncio/).

            
          
        
      
      
        
  
    #### Previous topic

    [Networking and Interprocess Communication](ipc.html)
  
  
    #### Next topic

    [Runners](asyncio-runner.html)
  
  
    ### This page

    

      - [Report a bug](../bugs.html)

      
        Show source
        
      
      
    

  
        

«

      
      
      
    
      ### Navigation

      

        
          [index](../genindex.html)
        
          [modules](../py-modindex.html) |
        
          [next](asyncio-runner.html) |
        
          [previous](ipc.html) |

          - 

          - [Python](https://www.python.org/) &#187;

          
            
            
          
          
              
          
    
      [3.14.3 Documentation](../index.html) &#187;
    

          - [The Python Standard Library](index.html) &#187;

          - [Networking and Interprocess Communication](ipc.html) &#187;

        - [`asyncio` — Asynchronous I/O]()

                
                    

    
        
          
          
        
    
                     |
                
            

    Theme
    
        Auto
        Light
        Dark
    
 |
            
      

      
    
    &copy; [Copyright](../copyright.html) 2001 Python Software Foundation.
    

    This page is licensed under the Python Software Foundation License Version 2.
    

    Examples, recipes, and other code in the documentation are additionally licensed under the Zero Clause BSD License.
    

    
      See [History and License](/license.html) for more information.

    
    
    

    The Python Software Foundation is a non-profit corporation.
[Please donate.](https://www.python.org/psf/donations/)

    

      Last updated on Feb 17, 2026 (12:56 UTC).
    
      [Found a bug](/bugs.html)?
    
    

    Created using [Sphinx](https://www.sphinx-doc.org/) 8.2.3.