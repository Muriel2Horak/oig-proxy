# GitHub - Shopify/toxiproxy: :alarm_clock: A TCP proxy to simulate network and system conditions for chaos and resiliency testing

> Source: https://github.com/Shopify/toxiproxy
> Cached: 2026-02-18T03:56:50.711Z

---

# Toxiproxy

[](#toxiproxy)
[](https://github.com/Shopify/toxiproxy/releases/latest)
[](https://github.com/Shopify/toxiproxy/actions/workflows/test.yml)
[](https://camo.githubusercontent.com/0529c4e7ec6f9842799a9150ba22a45318b533ca267c0ee12df8356a643f9d90/687474703a2f2f692e696d6775722e636f6d2f734f614e77306f2e706e67)

Toxiproxy is a framework for simulating network conditions. It's made
specifically to work in testing, CI and development environments, supporting
deterministic tampering with connections, but with support for randomized chaos
and customization. Toxiproxy is the tool you need to prove with tests that
your application doesn't have single points of failure. We've been
successfully using it in all development and test environments at Shopify since
October, 2014. See our [blog post](https://shopify.engineering/building-and-testing-resilient-ruby-on-rails-applications) on resiliency for more information.
Toxiproxy usage consists of two parts. A TCP proxy written in Go (what this
repository contains) and a client communicating with the proxy over HTTP. You
configure your application to make all test connections go through Toxiproxy
and can then manipulate their health via HTTP. See [Usage](#usage)
below on how to set up your project.
For example, to add 1000ms of latency to the response of MySQL from the Ruby
client:
Toxiproxy[:mysql_master].downstream(:latency, latency: 1000).apply do
  Shop.first # this takes at least 1s
end
To take down all Redis instances:

Toxiproxy[/redis/].down do
  Shop.first # this will throw an exception
end
While the examples in this README are currently in Ruby, there's nothing
stopping you from creating a client in any other language (see
[Clients](#clients)).
## Table of Contents

[](#table-of-contents)

[Toxiproxy](#toxiproxy)

- [Table of Contents](#table-of-contents)

- [Why yet another chaotic TCP proxy?](#why-yet-another-chaotic-tcp-proxy)

- [Clients](#clients)

- [Example](#example)

[Usage](#usage)

[1. Installing Toxiproxy](#1-installing-toxiproxy)

- [Upgrading from Toxiproxy 1.x](#upgrading-from-toxiproxy-1x)

- [2. Populating Toxiproxy](#2-populating-toxiproxy)

- [3. Using Toxiproxy](#3-using-toxiproxy)

- [4. Logging](#4-logging)

[Toxics](#toxics)

- [latency](#latency)

- [down](#down)

- [bandwidth](#bandwidth)

- [slow_close](#slow_close)

- [timeout](#timeout)

- [reset_peer](#reset_peer)

- [slicer](#slicer)

- [limit_data](#limit_data)

[HTTP API](#http-api)

- [Proxy fields:](#proxy-fields)

- [Toxic fields:](#toxic-fields)

- [Endpoints](#endpoints)

- [Populating Proxies](#populating-proxies)

- [CLI Example](#cli-example)

- [Metrics](#metrics)

- [Frequently Asked Questions](#frequently-asked-questions)

- [Development](#development)

- [Release](#release)

## Why yet another chaotic TCP proxy?

[](#why-yet-another-chaotic-tcp-proxy)
The existing ones we found didn't provide the kind of dynamic API we needed for
integration and unit testing. Linux tools like `nc` and so on are not
cross-platform and require root, which makes them problematic in test,
development and CI environments.
## Clients

[](#clients)

- [toxiproxy-ruby](https://github.com/Shopify/toxiproxy-ruby)

- [toxiproxy-go](https://github.com/Shopify/toxiproxy/tree/main/client)

- [toxiproxy-python](https://github.com/douglas/toxiproxy-python)

- [toxiproxy.net](https://github.com/mdevilliers/Toxiproxy.Net)

- [toxiproxy-php-client](https://github.com/ihsw/toxiproxy-php-client)

- [toxiproxy-node-client](https://github.com/ihsw/toxiproxy-node-client)

- [toxiproxy-java](https://github.com/trekawek/toxiproxy-java)

- [toxiproxy-haskell](https://github.com/jpittis/toxiproxy-haskell)

- [toxiproxy-rust](https://github.com/itarato/toxiproxy_rust)

- [toxiproxy-elixir](https://github.com/Jcambass/toxiproxy_ex)

## Example

[](#example)
Let's walk through an example with a Rails application. Note that Toxiproxy is
in no way tied to Ruby, it's just been our first use case. You can see the full example at
[sirupsen/toxiproxy-rails-example](https://github.com/sirupsen/toxiproxy-rails-example).
To get started right away, jump down to [Usage](#usage).
For our popular blog, for some reason we're storing the tags for our posts in
Redis and the posts themselves in MySQL. We might have a `Post` class that
includes some methods to manipulate tags in a [Redis set](http://redis.io/commands#set):
class Post < ActiveRecord::Base
  # Return an Array of all the tags.
  def tags
    TagRedis.smembers(tag_key)
  end

  # Add a tag to the post.
  def add_tag(tag)
    TagRedis.sadd(tag_key, tag)
  end

  # Remove a tag from the post.
  def remove_tag(tag)
    TagRedis.srem(tag_key, tag)
  end

  # Return the key in Redis for the set of tags for the post.
  def tag_key
    "post:tags:#{self.id}"
  end
end
We've decided that erroring while writing to the tag data store
(adding/removing) is OK. However, if the tag data store is down, we should be
able to see the post with no tags. We could simply rescue the
`Redis::CannotConnectError` around the `SMEMBERS` Redis call in the `tags`
method. Let's use Toxiproxy to test that.
Since we've already installed Toxiproxy and it's running on our machine, we can
skip to step 2. This is where we need to make sure Toxiproxy has a mapping for
Redis tags. To `config/boot.rb` (before any connection is made) we add:
require 'toxiproxy'

Toxiproxy.populate([
  {
    name: "toxiproxy_test_redis_tags",
    listen: "127.0.0.1:22222",
    upstream: "127.0.0.1:6379"
  }
])
Then in `config/environments/test.rb` we set the `TagRedis` to be a Redis client
that connects to Redis through Toxiproxy by adding this line:
TagRedis = Redis.new(port: 22222)
All calls in the test environment now go through Toxiproxy. That means we can
add a unit test where we simulate a failure:
test "should return empty array when tag redis is down when listing tags" do
  @post.add_tag "mammals"

  # Take down all Redises in Toxiproxy
  Toxiproxy[/redis/].down do
    assert_equal [], @post.tags
  end
end
The test fails with `Redis::CannotConnectError`. Perfect! Toxiproxy took down
the Redis successfully for the duration of the closure. Let's fix the `tags`
method to be resilient:
def tags
  TagRedis.smembers(tag_key)
rescue Redis::CannotConnectError
  []
end
The tests pass! We now have a unit test that proves fetching the tags when Redis
is down returns an empty array, instead of throwing an exception. For full
coverage you should also write an integration test that wraps fetching the
entire blog post page when Redis is down.
Full example application is at
[sirupsen/toxiproxy-rails-example](https://github.com/sirupsen/toxiproxy-rails-example).
## Usage

[](#usage)
Configuring a project to use Toxiproxy consists of three steps:

- Installing Toxiproxy

- Populating Toxiproxy

- Using Toxiproxy

### 1. Installing Toxiproxy

[](#1-installing-toxiproxy)
**Linux**

See [`Releases`](https://github.com/Shopify/toxiproxy/releases) for the latest
binaries and system packages for your architecture.
**Ubuntu**

$ wget -O toxiproxy-2.1.4.deb https://github.com/Shopify/toxiproxy/releases/download/v2.1.4/toxiproxy_2.1.4_amd64.deb
$ sudo dpkg -i toxiproxy-2.1.4.deb
$ sudo service toxiproxy start
**OS X**

With [Homebrew](https://brew.sh/):

$ brew tap shopify/shopify
$ brew install toxiproxy
Or with [MacPorts](https://www.macports.org/):

$ port install toxiproxy
**Windows**

Toxiproxy for Windows is available for download at [https://github.com/Shopify/toxiproxy/releases/download/v2.1.4/toxiproxy-server-windows-amd64.exe](https://github.com/Shopify/toxiproxy/releases/download/v2.1.4/toxiproxy-server-windows-amd64.exe)

**Docker**

Toxiproxy is available on [Github container registry](https://github.com/Shopify/toxiproxy/pkgs/container/toxiproxy).
Old versions `<= 2.1.4` are available on on [Docker Hub](https://hub.docker.com/r/shopify/toxiproxy/).
$ docker pull ghcr.io/shopify/toxiproxy
$ docker run --rm -it ghcr.io/shopify/toxiproxy
If using Toxiproxy from the host rather than other containers, enable host networking with `--net=host`.

$ docker run --rm --entrypoint="/toxiproxy-cli" -it ghcr.io/shopify/toxiproxy list
**Source**

If you have Go installed, you can build Toxiproxy from source using the make file:

$ make build
$ ./toxiproxy-server
#### Upgrading from Toxiproxy 1.x

[](#upgrading-from-toxiproxy-1x)
In Toxiproxy 2.0 several changes were made to the API that make it incompatible with version 1.x.
In order to use version 2.x of the Toxiproxy server, you will need to make sure your client
library supports the same version. You can check which version of Toxiproxy you are running by
looking at the `/version` endpoint.
See the documentation for your client library for specific library changes. Detailed changes
for the Toxiproxy server can been found in [CHANGELOG.md](/Shopify/toxiproxy/blob/main/CHANGELOG.md).
### 2. Populating Toxiproxy

[](#2-populating-toxiproxy)
When your application boots, it needs to make sure that Toxiproxy knows which
endpoints to proxy where. The main parameters are: name, address for Toxiproxy
to **listen** on and the address of the upstream.
Some client libraries have helpers for this task, which is essentially just
making sure each proxy in a list is created. Example from the Ruby client:
# Make sure `shopify_test_redis_master` and `shopify_test_mysql_master` are
# present in Toxiproxy
Toxiproxy.populate([
  {
    name: "shopify_test_redis_master",
    listen: "127.0.0.1:22220",
    upstream: "127.0.0.1:6379"
  },
  {
    name: "shopify_test_mysql_master",
    listen: "127.0.0.1:24220",
    upstream: "127.0.0.1:3306"
  }
])
This code needs to run as early in boot as possible, before any code establishes
a connection through Toxiproxy. Please check your client library for
documentation on the population helpers.
Alternatively use the CLI to create proxies, e.g.:

toxiproxy-cli create -l localhost:26379 -u localhost:6379 shopify_test_redis_master
We recommend a naming such as the above: `<app>_<env>_<data store>_<shard>`.
This makes sure there are no clashes between applications using the same
Toxiproxy.
For large application we recommend storing the Toxiproxy configurations in a
separate configuration file. We use `config/toxiproxy.json`. This file can be
passed to the server using the `-config` option, or loaded by the application
to use with the `populate` function.
An example `config/toxiproxy.json`:

[
  {
    "name": "web_dev_frontend_1",
    "listen": "[::]:18080",
    "upstream": "webapp.domain:8080",
    "enabled": true
  },
  {
    "name": "web_dev_mysql_1",
    "listen": "[::]:13306",
    "upstream": "database.domain:3306",
    "enabled": true
  }
]
Use ports outside the ephemeral port range to avoid random port conflicts.
It's `32,768` to `61,000` on Linux by default, see
`/proc/sys/net/ipv4/ip_local_port_range`.
### 3. Using Toxiproxy

[](#3-using-toxiproxy)
To use Toxiproxy, you now need to configure your application to connect through
Toxiproxy. Continuing with our example from step two, we can configure our Redis
client to connect through Toxiproxy:
# old straight to redis
redis = Redis.new(port: 6380)

# new through toxiproxy
redis = Redis.new(port: 22220)
Now you can tamper with it through the Toxiproxy API. In Ruby:

redis = Redis.new(port: 22220)

Toxiproxy[:shopify_test_redis_master].downstream(:latency, latency: 1000).apply do
  redis.get("test") # will take 1s
end
Or via the CLI:

toxiproxy-cli toxic add -t latency -a latency=1000 shopify_test_redis_master
Please consult your respective client library on usage.

### 4. Logging

[](#4-logging)
There are the following log levels: panic, fatal, error, warn or warning, info, debug and trace.
The level could be updated via environment variable `LOG_LEVEL`.
### Toxics

[](#toxics)
Toxics manipulate the pipe between the client and upstream. They can be added
and removed from proxies using the [HTTP api](#http-api). Each toxic has its own parameters
to change how it affects the proxy links.
For documentation on implementing custom toxics, see [CREATING_TOXICS.md](/Shopify/toxiproxy/blob/main/CREATING_TOXICS.md)

#### latency

[](#latency)
Add a delay to all data going through the proxy. The delay is equal to `latency` +/- `jitter`.

Attributes:

- `latency`: time in milliseconds

- `jitter`: time in milliseconds

#### down

[](#down)
Bringing a service down is not technically a toxic in the implementation of
Toxiproxy. This is done by `POST`ing to `/proxies/{proxy}` and setting the
`enabled` field to `false`.
#### bandwidth

[](#bandwidth)
Limit a connection to a maximum number of kilobytes per second.

Attributes:

- `rate`: rate in KB/s

#### slow_close

[](#slow_close)
Delay the TCP socket from closing until `delay` has elapsed.

Attributes:

- `delay`: time in milliseconds

#### timeout

[](#timeout)
Stops all data from getting through, and closes the connection after `timeout`. If
`timeout` is 0, the connection won't close, and data will be dropped until the
toxic is removed.
Attributes:

- `timeout`: time in milliseconds

#### reset_peer

[](#reset_peer)
Simulate TCP RESET (Connection reset by peer) on the connections by closing the stub Input
immediately or after a `timeout`.
Attributes:

- `timeout`: time in milliseconds

#### slicer

[](#slicer)
Slices TCP data up into small bits, optionally adding a delay between each
sliced "packet".
Attributes:

- `average_size`: size in bytes of an average packet

- `size_variation`: variation in bytes of an average packet (should be smaller than average_size)

- `delay`: time in microseconds to delay each packet by

#### limit_data

[](#limit_data)
Closes connection when transmitted data exceeded limit.

- `bytes`: number of bytes it should transmit before connection is closed

### HTTP API

[](#http-api)
All communication with the Toxiproxy daemon from the client happens through the
HTTP interface, which is described here.
Toxiproxy listens for HTTP on port **8474**.

#### Proxy fields:

[](#proxy-fields)

- `name`: proxy name (string)

- `listen`: listen address (string)

- `upstream`: proxy upstream address (string)

- `enabled`: true/false (defaults to true on creation)

To change a proxy's name, it must be deleted and recreated.

Changing the `listen` or `upstream` fields will restart the proxy and drop any active connections.

If `listen` is specified with a port of 0, toxiproxy will pick an ephemeral port. The `listen` field
in the response will be updated with the actual port.
If you change `enabled` to `false`, it will take down the proxy. You can switch it
back to `true` to reenable it.
#### Toxic fields:

[](#toxic-fields)

- `name`: toxic name (string, defaults to `<type>_<stream>`)

- `type`: toxic type (string)

- `stream`: link direction to affect (defaults to `downstream`)

- `toxicity`: probability of the toxic being applied to a link (defaults to 1.0, 100%)

- `attributes`: a map of toxic-specific attributes

See [Toxics](#toxics) for toxic-specific attributes.

T

... [Content truncated]