# Untitled

> Source: https://oneuptime.com/blog/post/2026-01-25-network-timeout-strategies/view
> Cached: 2026-02-17T19:33:58.394Z

---

Timeouts are your defense against hung connections, unresponsive services, and cascading failures. Without proper timeouts, a single slow dependency can exhaust your connection pools and bring down your entire system. This guide shows you how to configure timeouts at every layer of your stack.

## Why Timeouts Matter

Consider what happens without timeouts:

- Service A calls Service B
- Service B hangs (database lock, infinite loop, network issue)
- Service A's thread blocks forever waiting
- More requests come in, more threads block
- Service A runs out of threads/connections
- Service A becomes unresponsive
- Services calling A also hang

Proper timeouts break this chain.

```
flowchart TD
    subgraph Without["Without Timeouts"]
        A1[Request 1] --> B1[Waiting...]
        A2[Request 2] --> B2[Waiting...]
        A3[Request N] --> B3[Waiting...]
        B1 --> C1[Thread Pool Exhausted]
        B2 --> C1
        B3 --> C1
        C1 --> D1[Service Down]
    end

    subgraph With["With Timeouts"]
        E1[Request 1] --> F1[Timeout after 5s]
        F1 --> G1[Return Error]
        G1 --> H1[Service Healthy]
    end
```

## Types of Timeouts

Different timeouts protect against different failure modes:

        Timeout TypePurposeTypical ValueConnection TimeoutTime to establish TCP connection1-5 secondsRead TimeoutTime to receive response data5-30 secondsWrite TimeoutTime to send request data5-30 secondsIdle TimeoutTime a connection can be idle60-300 secondsRequest TimeoutTotal time for entire request10-60 seconds
      ## HTTP Client Timeouts

### Python requests

```
# python_timeouts.py - Configure timeouts in Python HTTP clients
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Basic timeout configuration
def make_request_simple():
    # timeout=(connect_timeout, read_timeout)
    response = requests.get(
        'https://api.example.com/data',
        timeout=(3.05, 10)  # 3s connect, 10s read
    )
    return response.json()

# Production-ready client with comprehensive timeouts
class ResilientHTTPClient:
    def __init__(self):
        self.session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504]
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )

        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get(self, url, **kwargs):
        # Set default timeouts if not provided
        kwargs.setdefault('timeout', (5, 30))
        return self.session.get(url, **kwargs)

    def post(self, url, **kwargs):
        kwargs.setdefault('timeout', (5, 30))
        return self.session.post(url, **kwargs)

# Usage
client = ResilientHTTPClient()
response = client.get('https://api.example.com/users')
```

### Go HTTP client

```
// go_timeouts.go - Configure timeouts in Go HTTP client
package main

import (
    "context"
    "net"
    "net/http"
    "time"
)

func createHTTPClient() *http.Client {
    // Custom transport with granular timeouts
    transport := &http.Transport{
        // Connection timeout
        DialContext: (&net.Dialer{
            Timeout:   5 * time.Second,  // TCP connect timeout
            KeepAlive: 30 * time.Second, // TCP keepalive interval
        }).DialContext,

        // TLS handshake timeout
        TLSHandshakeTimeout: 5 * time.Second,

        // Connection pool settings
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 10,
        IdleConnTimeout:     90 * time.Second,

        // Response header timeout (time to receive headers)
        ResponseHeaderTimeout: 10 * time.Second,

        // Expect continue timeout
        ExpectContinueTimeout: 1 * time.Second,
    }

    client := &http.Client{
        Transport: transport,
        // Total request timeout (overrides individual timeouts)
        Timeout: 30 * time.Second,
    }

    return client
}

// Request with context timeout (recommended)
func makeRequestWithContext() error {
    client := createHTTPClient()

    // Context timeout gives you more control
    ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
    defer cancel()

    req, err := http.NewRequestWithContext(ctx, "GET", "https://api.example.com/data", nil)
    if err != nil {
        return err
    }

    resp, err := client.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    return nil
}
```

## Database Connection Timeouts

### PostgreSQL

```
# postgres_timeouts.py - PostgreSQL timeout configuration
import psycopg2
from psycopg2 import pool

# Connection string with timeouts
conn_params = {
    'host': 'db.example.com',
    'database': 'myapp',
    'user': 'appuser',
    'password': 'secret',
    # Connection timeout in seconds
    'connect_timeout': 5,
    # Statement timeout (query execution limit)
    'options': '-c statement_timeout=30000',  # 30 seconds in ms
}

# Create connection
conn = psycopg2.connect(**conn_params)

# Set session-level timeouts
with conn.cursor() as cur:
    # Lock wait timeout
    cur.execute("SET lock_timeout = '10s'")
    # Idle transaction timeout
    cur.execute("SET idle_in_transaction_session_timeout = '60s'")

# Connection pool with timeouts
connection_pool = pool.ThreadedConnectionPool(
    minconn=5,
    maxconn=20,
    **conn_params
)

def get_connection_with_timeout(timeout_seconds=5):
    """Get connection from pool with timeout"""
    import time
    start = time.time()

    while time.time() - start  TimeoutConfig:
    """Get appropriate timeouts based on service tier"""
    configs = {
        ServiceTier.CRITICAL: TimeoutConfig(
            connect=2.0,
            read=10.0,
            write=5.0,
            total=15.0
        ),
        ServiceTier.STANDARD: TimeoutConfig(
            connect=3.0,
            read=30.0,
            write=10.0,
            total=45.0
        ),
        ServiceTier.BACKGROUND: TimeoutConfig(
            connect=5.0,
            read=60.0,
            write=30.0,
            total=120.0
        ),
    }
    return configs[tier]
```

## Monitoring Timeout Metrics

Track timeouts to identify problems:

```
# timeout_metrics.py - Prometheus metrics for timeout observability
from prometheus_client import Counter, Histogram

# Count timeouts by type
timeout_total = Counter(
    'http_client_timeout_total',
    'Total timeout errors',
    ['service', 'timeout_type']  # connect, read, write, total
)

# Track request duration to tune timeouts
request_duration = Histogram(
    'http_client_request_duration_seconds',
    'Request duration in seconds',
    ['service', 'method', 'endpoint'],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120]
)

# Alert rules
ALERT_RULES = """
groups:
  - name: timeout_alerts
    rules:
      - alert: HighTimeoutRate
        expr: rate(http_client_timeout_total[5m]) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High timeout rate for {{ $labels.service }}"

      - alert: P99LatencyNearTimeout
        expr: histogram_quantile(0.99, rate(http_client_request_duration_seconds_bucket[5m])) > 25
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P99 latency approaching timeout threshold"
"""
```

## Best Practices

**Set explicit timeouts everywhere** - Never rely on defaults. Defaults vary and are often too long.

**Use defense in depth** - Set timeouts at multiple layers (application, load balancer, database).

**Make timeouts configurable** - Allow adjustment without code changes for different environments.

**Differentiate by endpoint** - Slow operations (reports, uploads) need different timeouts than fast operations.

**Monitor p99 latency** - Set timeouts above p99 to avoid cutting off legitimate slow requests.

**Include buffer for retries** - Total timeout should allow time for retry attempts.

**Test timeout behavior** - Verify your application handles timeouts gracefully.

## Conclusion

Network timeouts are essential for building resilient systems. Configure timeouts at every layer, from HTTP clients to databases to load balancers. Set appropriate values based on expected latency plus buffer, monitor timeout rates to detect issues, and ensure your application gracefully handles timeout errors. Without proper timeouts, a single slow dependency can cascade into a complete system failure.

                                
                            

                            
                            
                                Share this article
                                
                                    
                                        
                                    
                                    
                                        
                                    
                                    
                                        
                                            
                                            
                                        
                                    
                                
                            

                            
                            
                            
                                
                                    
                                    
                                        
                                        
                                            
                                        
                                    
                                    
                                        
                                            ### Nawaz Dhandala

                                            Author
                                        
                                        @nawazdhandala • Jan 25, 2026 • 

                                        Nawaz is building OneUptime with a passion for engineering reliable systems and improving observability.
                                        
                                            
                                                
                                                GitHub
                                            
                                        
                                    
                                
                            
                            

                            
                                Our Commitment to Open Source

    

        
            

                
                    
                   

                        Everything we do at OneUptime is 100% open-source. You can contribute by writing a post just like this.
                        Please check contributing guidelines [here.](https://github.com/oneuptime/blog)

                            

                                If you wish to contribute to this post, you can make edits and improve it [here](https://github.com/oneuptime/blog/tree/master/posts/2026-01-25-network-timeout-strategies).