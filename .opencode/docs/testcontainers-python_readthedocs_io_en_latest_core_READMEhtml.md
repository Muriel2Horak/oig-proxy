# Testcontainers Core &#8212; testcontainers 2.0.0 documentation

> Source: https://testcontainers-python.readthedocs.io/en/latest/core/README.html
> Cached: 2026-02-18T03:57:00.148Z

---

# Testcontainers Core[¶](#testcontainers-core)

`testcontainers-core` is the core functionality for spinning up Docker containers in test environments.

*class *DockerContainer(*image: [str](https://docs.python.org/3/library/stdtypes.html#str)*, *docker_client_kw: [dict](https://docs.python.org/3/library/stdtypes.html#dict) | [None](https://docs.python.org/3/library/constants.html#None) = None*, ***kwargs*)[¶](#testcontainers.core.container.DockerContainer)
Basic container object to spin up Docker instances.

>>> from testcontainers.core.container import DockerContainer
>>> from testcontainers.core.waiting_utils import wait_for_logs

>>> with DockerContainer("hello-world") as container:
...    delay = wait_for_logs(container, "Hello from Docker!")

*class *DockerImage(*path: [str](https://docs.python.org/3/library/stdtypes.html#str) | [PathLike](https://docs.python.org/3/library/os.html#os.PathLike)*, *docker_client_kw: [dict](https://docs.python.org/3/library/stdtypes.html#dict) | [None](https://docs.python.org/3/library/constants.html#None) = None*, *tag: [str](https://docs.python.org/3/library/stdtypes.html#str) | [None](https://docs.python.org/3/library/constants.html#None) = None*, *clean_up: [bool](https://docs.python.org/3/library/functions.html#bool) = True*, *dockerfile_path: [str](https://docs.python.org/3/library/stdtypes.html#str) | [PathLike](https://docs.python.org/3/library/os.html#os.PathLike) = 'Dockerfile'*, *no_cache: [bool](https://docs.python.org/3/library/functions.html#bool) = False*, ***kwargs*)[¶](#testcontainers.core.image.DockerImage)
Basic image object to build Docker images.

>>> from testcontainers.core.image import DockerImage

>>> with DockerImage(path="./core/tests/image_fixtures/sample/", tag="test-image") as image:
...    logs = image.get_logs()

Parameters:

**tag** – Tag for the image to be built (default: None)

**path** – Path to the build context

**dockerfile_path** – Path to the Dockerfile within the build context path (default: Dockerfile)

**no_cache** – Bypass build cache; CLI’s –no-cache

*class *DbContainer(*image: [str](https://docs.python.org/3/library/stdtypes.html#str)*, *docker_client_kw: [dict](https://docs.python.org/3/library/stdtypes.html#dict) | [None](https://docs.python.org/3/library/constants.html#None) = None*, ***kwargs*)[¶](#testcontainers.core.generic.DbContainer)
**DEPRECATED (for removal)**

Generic database container.

## Examples[¶](#examples)

Using DockerContainer and DockerImage to create a container:

>>> from testcontainers.core.container import DockerContainer
>>> from testcontainers.core.waiting_utils import wait_for_logs
>>> from testcontainers.core.image import DockerImage

>>> with DockerImage(path="./core/tests/image_fixtures/sample/", tag="test-sample:latest") as image:
...     with DockerContainer(str(image)) as container:
...         delay = wait_for_logs(container, "Test Sample Image")

The DockerImage class is used to build the image from the specified path and tag.
The DockerContainer class is then used to create a container from the image.

          
          
        
      
      
        
# [testcontainers](../index.html)

### Navigation

- [Testcontainers Core](#)

- [Community Modules](../modules/index.html)

### Related Topics

  [Documentation overview](../index.html)

      - Previous: [testcontainers-python](../index.html)

      - Next: [Community Modules](../modules/index.html)

  

  ### Quick search

    
    
      
      
    
    

        
      
      
    
    
      &#169;2017-2024, Sergey Pirogov and Testcontainers Python contributors.
      
      |
      Powered by [Sphinx 7.2.6](https://www.sphinx-doc.org/)
      & [Alabaster 0.7.16](https://alabaster.readthedocs.io)
      
      |
      [Page source](../_sources/core/README.rst.txt)