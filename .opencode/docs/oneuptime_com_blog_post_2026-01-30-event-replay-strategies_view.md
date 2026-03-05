# Untitled

> Source: https://oneuptime.com/blog/post/2026-01-30-event-replay-strategies/view
> Cached: 2026-02-17T19:30:39.585Z

---

Your payment service crashed. Thousands of transactions processed but downstream systems never got the events. The events are sitting in your event store, but how do you replay them without duplicating charges or corrupting state?

This is the problem event replay solves. Done right, it turns catastrophic failures into minor inconveniences. Done wrong, it creates more problems than it fixes.

## Why Event Replay Matters

Event-driven architectures decouple services through asynchronous messaging. Events flow through queues, streams, or logs. When something breaks, you need a way to reprocess events without starting from scratch.

Common scenarios requiring replay:

- Consumer service went down and missed events
- Bug in processing logic corrupted downstream data
- New service needs historical data to initialize state
- Audit requirements demand reprocessing with updated logic
- Testing production event flows in staging

## Event Replay Architecture Overview

```
flowchart TB
    subgraph Event Sources
        P1[Producer 1]
        P2[Producer 2]
        P3[Producer 3]
    end

    subgraph Event Store
        ES[(Event Log)]
        ES --> |Append Only| ES
    end

    subgraph Replay Controller
        RC[Replay Service]
        RC --> |Read| ES
        RC --> |Filter| F[Event Filter]
        F --> |Route| R[Event Router]
    end

    subgraph Consumers
        C1[Consumer A]
        C2[Consumer B]
        C3[Consumer C]
    end

    P1 --> ES
    P2 --> ES
    P3 --> ES
    R --> C1
    R --> C2
    R --> C3

    style ES fill:#e1f5fe
    style RC fill:#fff3e0
```

The key components:

- **Event Store**: Immutable log of all events with timestamps and sequence numbers
- **Replay Controller**: Service that reads from the store and republishes events
- **Event Filter**: Selects which events to replay based on criteria
- **Event Router**: Directs replayed events to appropriate consumers

## Three Replay Strategies

### 1. Selective Replay

Replay specific events based on filters like event type, time range, or entity ID. Use this when you know exactly what needs reprocessing.

```
flowchart LR
    ES[(Event Store)] --> F{Filter}
    F --> |type=OrderCreated| R[Replay Queue]
    F --> |type=PaymentFailed| R
    F --> |Discard others| X[/Skip/]
    R --> C[Consumer]

    style F fill:#ffecb3
```

**When to use:**

- Single consumer missed specific event types
- Need to reprocess events for a specific customer
- Bug affected only certain event categories

**Implementation:**

```
interface ReplayFilter {
  eventTypes?: string[];
  startTime?: Date;
  endTime?: Date;
  entityIds?: string[];
  correlationIds?: string[];
}

class SelectiveReplayService {
  constructor(
    private eventStore: EventStore,
    private publisher: EventPublisher
  ) {}

  async replay(filter: ReplayFilter, targetQueue: string): Promise {
    const events = await this.eventStore.query({
      types: filter.eventTypes,
      from: filter.startTime,
      to: filter.endTime,
      entities: filter.entityIds
    });

    let processed = 0;
    let failed = 0;

    for (const event of events) {
      try {
        // Mark as replay to prevent side effects
        const replayEvent = {
          ...event,
          metadata: {
            ...event.metadata,
            isReplay: true,
            originalTimestamp: event.timestamp,
            replayTimestamp: new Date()
          }
        };

        await this.publisher.publish(targetQueue, replayEvent);
        processed++;
      } catch (error) {
        failed++;
        console.error(`Failed to replay event ${event.id}:`, error);
      }
    }

    return { processed, failed, total: events.length };
  }
}
```

### 2. Full Replay

Rebuild entire system state by replaying all events from the beginning. Use this for disaster recovery or initializing new services.

```
flowchart TB
    subgraph Full Replay Process
        ES[(Event Store)] --> |Read All| RC[Replay Controller]
        RC --> |Batch| B1[Batch 1]
        RC --> |Batch| B2[Batch 2]
        RC --> |Batch| B3[Batch N]
    end

    subgraph Consumer State
        B1 --> S[State Store]
        B2 --> S
        B3 --> S
        S --> |Rebuilt| NS[New State]
    end

    style ES fill:#e1f5fe
    style NS fill:#c8e6c9
```

**When to use:**

- Database corruption requires complete rebuild
- Deploying new read model or projection
- Migrating to new data schema
- Creating new aggregate from historical events

**Implementation:**

```
class FullReplayService {
  constructor(
    private eventStore: EventStore,
    private stateStore: StateStore
  ) {}

  async fullReplay(
    aggregateType: string,
    batchSize: number = 1000
  ): Promise {
    // Clear existing state
    await this.stateStore.clear(aggregateType);

    let offset = 0;
    let hasMore = true;

    while (hasMore) {
      const batch = await this.eventStore.readBatch({
        aggregateType,
        offset,
        limit: batchSize
      });

      if (batch.length === 0) {
        hasMore = false;
        continue;
      }

      // Process batch with transaction
      await this.stateStore.transaction(async (tx) => {
        for (const event of batch) {
          await this.applyEvent(event, tx);
        }
      });

      offset += batch.length;
      console.log(`Processed ${offset} events`);
    }

    console.log(`Full replay complete. Total events: ${offset}`);
  }

  private async applyEvent(event: Event, tx: Transaction): Promise {
    const handler = this.getHandler(event.type);
    if (handler) {
      await handler(event, tx);
    }
  }
}
```

### 3. Point-in-Time Recovery

Rebuild state as it existed at a specific moment. Critical for auditing, debugging, and regulatory compliance.

```
flowchart TB
    subgraph Timeline
        T1[Event 1
10:00] --> T2[Event 2
10:15]
        T2 --> T3[Event 3
10:30]
        T3 --> T4[Event 4
10:45]
        T4 --> T5[Event 5
11:00]
    end

    subgraph Point-in-Time Query
        Q[Query: State at 10:35]
        Q --> |Include| T1
        Q --> |Include| T2
        Q --> |Include| T3
        Q --> |Exclude| T4
        Q --> |Exclude| T5
    end

    subgraph Result
        R[State reflects
Events 1-3 only]
    end

    T3 --> R

    style Q fill:#ffecb3
    style R fill:#c8e6c9
```

**When to use:**

- Investigating what state looked like when bug occurred
- Regulatory audit requiring historical snapshots
- Comparing state before and after specific changes
- Rolling back to known good state

**Implementation:**

```
class PointInTimeRecovery {
  constructor(private eventStore: EventStore) {}

  async getStateAt(
    aggregateId: string,
    timestamp: Date
  ): Promise {
    // Get all events up to the target timestamp
    const events = await this.eventStore.query({
      aggregateId,
      to: timestamp,
      orderBy: 'timestamp',
      order: 'asc'
    });

    if (events.length === 0) {
      return null;
    }

    // Rebuild state by applying events in order
    let state: T = this.getInitialState();

    for (const event of events) {
      state = this.applyEvent(state, event);
    }

    return state;
  }

  async compareStates(
    aggregateId: string,
    time1: Date,
    time2: Date
  ): Promise {
    const before = await this.getStateAt(aggregateId, time1);
    const after = await this.getStateAt(aggregateId, time2);

    return {
      before,
      after,
      diff: this.calculateDiff(before, after)
    };
  }

  private applyEvent(state: T, event: Event): T {
    // Event sourcing reducer pattern
    switch (event.type) {
      case 'OrderCreated':
        return { ...state, ...event.data, status: 'created' } as T;
      case 'OrderUpdated':
        return { ...state, ...event.data } as T;
      case 'OrderCancelled':
        return { ...state, status: 'cancelled' } as T;
      default:
        return state;
    }
  }
}
```

## Handling Idempotency

Replay without idempotency guarantees leads to duplicate processing. Every consumer must handle repeated events gracefully.

```
flowchart TB
    E[Incoming Event] --> C{Check Idempotency Key}
    C --> |Already Processed| S[Skip Event]
    C --> |New Event| P[Process Event]
    P --> R[Record Idempotency Key]
    R --> A[Acknowledge]
    S --> A

    style C fill:#ffecb3
```

**Implementation patterns:**

```
class IdempotentConsumer {
  constructor(
    private idempotencyStore: IdempotencyStore,
    private handler: EventHandler
  ) {}

  async process(event: Event): Promise {
    const idempotencyKey = this.getIdempotencyKey(event);

    // Check if already processed
    const existing = await this.idempotencyStore.get(idempotencyKey);
    if (existing) {
      console.log(`Event ${event.id} already processed, skipping`);
      return;
    }

    // Process the event
    await this.handler.handle(event);

    // Record successful processing
    await this.idempotencyStore.set(idempotencyKey, {
      eventId: event.id,
      processedAt: new Date(),
      result: 'success'
    });
  }

  private getIdempotencyKey(event: Event): string {
    // Use event ID combined with consumer identifier
    return `${this.handler.name}:${event.id}`;
  }
}
```

## Replay with Side Effects

Some events trigger external actions: sending emails, calling APIs, charging credit cards. During replay, you usually want to skip these.

```
flowchart TB
    E[Event] --> C{Is Replay?}
    C --> |Yes| P1[Process State Only]
    C --> |No| P2[Process State + Side Effects]

    P1 --> U[Update Database]
    P2 --> U
    P2 --> SE[Send Email]
    P2 --> API[Call External API]

    style C fill:#ffecb3
    style SE fill:#ffcdd2
    style API fill:#ffcdd2
```

**Implementation:**

```
class ReplayAwareHandler {
  async handle(event: Event): Promise {
    const isReplay = event.metadata?.isReplay === true;

    // Always update internal state
    await this.updateState(event);

    // Skip side effects during replay
    if (!isReplay) {
      await this.performSideEffects(event);
    }
  }

  private async updateState(event: Event): Promise {
    // Database updates, state changes
    await this.repository.save(event.data);
  }

  private async performSideEffects(event: Event): Promise {
    // External calls, notifications
    if (event.type === 'OrderCompleted') {
      await this.emailService.sendConfirmation(event.data.customerId);
      await this.inventoryService.reserve(event.data.items);
    }
  }
}
```

## Building a Replay Service

Here is a complete replay service implementation:

```
interface ReplayRequest {
  id: string;
  strategy: 'selective' | 'full' | 'point-in-time';
  filter?: ReplayFilter;
  targetTimestamp?: Date;
  targetConsumers: string[];
  options: {
    batchSize: number;
    delayBetweenBatches: number;
    skipSideEffects: boolean;
  };
}

interface ReplayStatus {
  id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress: {
    total: number;
    processed: number;
    failed: number;
  };
  startedAt?: Date;
  completedAt?: Date;
  error?: string;
}

class ReplayService {
  private activeReplays: Map = new Map();

  constructor(
    private eventStore: EventStore,
    private publisher: EventPublisher,
    private statusStore: ReplayStatusStore
  ) {}

  async startReplay(request: ReplayRequest): Promise {
    const status: ReplayStatus = {
      id: request.id,
      status: 'pending',
      progress: { total: 0, processed: 0, failed: 0 }
    };

    this.activeReplays.set(request.id, status);
    await this.statusStore.save(status);

    // Run replay asynchronously
    this.executeReplay(request).catch(error => {
      this.updateStatus(request.id, {
        status: 'failed',
        error: error.message
      });
    });

    return request.id;
  }

  async getStatus(replayId: string): Promise {
    return this.activeReplays.get(replayId) ||
           await this.statusStore.get(replayId);
  }

  private async executeReplay(request: ReplayRequest): Promise {
    this.updateStatus(request.id, { status: 'running', startedAt: new Date() });

    const events = await this.queryEvents(request);
    this.updateStatus(request.id, {
      progress: { total: events.length, processed: 0, failed: 0 }
    });

    const batches = this.createBatches(events, request.options.batchSize);

    for (const batch of batches) {
      await this.processBatch(request, batch);
      await this.delay(request.options.delayBetweenBatches);
    }

    this.updateStatus(request.id, { status: 'completed', completedAt: new Date() });
  }

  private async queryEvents(request: ReplayRequest): Promise {
    switch (request.strategy) {
      case 'selective':
        return this.eventStore.query(request.filter!);
      case 'full':
        return this.eventStore.readAll();
      case 'point-in-time':
        return this.eventStore.query({ to: request.targetTimestamp });
      default:
        throw new Error(`Unknown strategy: ${request.strategy}`);
    }
  }

  private async processBatch(request: ReplayRequest, batch: Event[]): Promise {
    const status = this.activeReplays.get(request.id)!;

    for (const event of batch) {
      try {
        const replayEvent = this.wrapForReplay(event, request);

        for (const consumer of request.targetConsumers) {
          await this.publisher.publish(consumer, replayEvent);
        }

        status.progress.processed++;
      } catch (error) {
        status.progress.failed++;
        console.error(`Replay failed for event ${event.id}:`, error);
      }
    }

    await this.statusStore.save(status);
  }

  private wrapForReplay(event: Event, request: ReplayRequest): Event {
    return {
      ...event,
      metadata: {
        ...event.metadata,
        isReplay: true,
        replayId: request.id,
        skipSideEffects: request.options.skipSideEffects,
        originalTimestamp: event.timestamp,
        replayTimestamp: new Date()
      }
    };
  }
}
```

## Replay Workflow Diagram

```
sequenceDiagram
    participant Op as Operator
    participant RS as Replay Service
    participant ES as Event Store
    participant Q as Message Queue
    participant C as Consumer

    Op->>RS: Start Replay Request
    RS->>RS: Validate Request
    RS->>ES: Query Events (with filters)
    ES-->>RS: Return Events

    loop For Each Batch
        RS->>RS: Mark Events as Replay
        RS->>Q: Publish Batch
        Q->>C: Deliver Events
        C->>C: Check Idempotency
        C->>C: Process (skip side effects)
        C-->>Q: Acknowledge
    end

    RS->>Op: Replay Complete
```

## Monitoring Replays

Track replay operations to catch issues early:

```
interface ReplayMetrics {
  replayId: string;
  eventsPerSecond: number;
  avgProcessingTime: number;
  errorRate: number;
  queueDepth: number;
}

class ReplayMonitor {
  async collectMetrics(replayId: string): Promise {
    const status = await this.replayService.getStatus(replayId);
    const queueStats = await this.queueService.getStats();

    return {
      replayId,
      ev

... [Content truncated]