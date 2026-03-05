# Write-Ahead Logging

> Source: https://sqlite.org/wal.html
> Cached: 2026-02-17T19:30:31.024Z

---

Write-Ahead Logging

Small. Fast. Reliable.
Choose any three.

[Home](index.html)
[Menu](javascript:void(0))
About
[Documentation](docs.html)
[Download](download.html)
License
[Support](support.html)
[Purchase](prosupport.html)

[Search](javascript:void(0))

About
Documentation
Download
Support
Purchase

Search Documentation
Search Changelog

Write-Ahead Logging

Table Of Contents
[1. Overview](#overview)
[2. How WAL Works](#how_wal_works)
[2.1. Checkpointing](#checkpointing)
[2.2. Concurrency](#concurrency)
[2.3. Performance Considerations](#performance_considerations)
[3. Activating And Configuring WAL Mode](#activating_and_configuring_wal_mode)
[3.1. Automatic Checkpoint](#automatic_checkpoint)
[3.2. Application-Initiated Checkpoints](#application_initiated_checkpoints)
[3.3. Persistence of WAL mode](#persistence_of_wal_mode)
[4. The WAL File](#the_wal_file)
[5. Read-Only Databases](#read_only_databases)
[6. Avoiding Excessively Large WAL Files](#avoiding_excessively_large_wal_files)
[7. Implementation Of Shared-Memory For The WAL-Index](#implementation_of_shared_memory_for_the_wal_index)
[8. Use of WAL Without Shared-Memory](#use_of_wal_without_shared_memory)
[9. Sometimes Queries Return SQLITE_BUSY In WAL Mode](#sometimes_queries_return_sqlite_busy_in_wal_mode)
[10. Backwards Compatibility](#backwards_compatibility)

# 1. Overview

The default method by which SQLite implements
[atomic commit and rollback](atomiccommit.html) is a [rollback journal](lockingv3.html#rollback).
Beginning with [version 3.7.0](releaselog/3_7_0.html) (2010-07-21), a new "Write-Ahead Log" option
(hereafter referred to as "WAL") is available.

There are advantages and disadvantages to using WAL instead of
a rollback journal.  Advantages include:

WAL is significantly faster in most scenarios.
WAL provides more concurrency as readers do not block writers and 
    a writer does not block readers.  Reading and writing can proceed 
    concurrently.
Disk I/O operations tends to be more sequential using WAL.
WAL uses many fewer fsync() operations and is thus less vulnerable to
    problems on systems where the fsync() system call is broken.

But there are also disadvantages:

All processes using a database must be on the same host computer;
    WAL does not work over a network filesystem.  This is because WAL requires
    all processes to share a small amount of memory and processes on
    separate host machines obviously cannot share memory with each other.
Transactions that involve changes against multiple [ATTACHed](lang_attach.html)
    databases are atomic for each individual database, but are not
    atomic across all databases as a set.
It is not possible to change the [page_size](pragma.html#pragma_page_size) after entering WAL
    mode, either on an empty database or by using [VACUUM](lang_vacuum.html) or by restoring
    from a backup using the [backup API](backup.html).  You must be in a rollback journal
    mode to change the page size.
It is not possible to open [read-only WAL databases](wal.html#readonly).
    The opening process must have write privileges for "-shm"
    [wal-index](walformat.html#shm) shared memory file associated with the database, if that
    file exists, or else write access on the directory containing
    the database file if the "-shm" file does not exist.
    Beginning with [version 3.22.0](releaselog/3_22_0.html) (2018-01-22), a read-only 
    WAL-mode database file can be opened if
    the -shm and -wal files
    already exist or those files can be created or the
    [database is immutable](uri.html#uriimmutable).
WAL might be very slightly slower (perhaps 1% or 2% slower)
    than the traditional rollback-journal approach
    in applications that do mostly reads and seldom write.
There is an additional quasi-persistent "-wal" file and
    "-shm" shared memory file associated with each
    database, which can make SQLite less appealing for use as an 
    [application file-format](appfileformat.html).
There is the extra operation of [checkpointing](wal.html#ckpt) which, though automatic
    by default, is still something that application developers need to
    be mindful of.
WAL works best with smaller transactions.  WAL does
    not work well for very large transactions.  For transactions larger than
    about 100 megabytes, traditional rollback journal modes will likely
    be faster.  For transactions in excess of a gigabyte, WAL mode may 
    fail with an I/O or disk-full error.
    It is recommended that one of the rollback journal modes be used for
    transactions larger than a few dozen megabytes.
    Beginning with [version 3.11.0](releaselog/3_11_0.html) (2016-02-15), 
    WAL mode works as efficiently with
    large transactions as does rollback mode.
    

# 2. How WAL Works

The traditional rollback journal works by writing a copy of the
original unchanged database content into a separate rollback journal file
and then writing changes directly into the database file.  In the
event of a crash or [ROLLBACK](lang_transaction.html), the original content contained in the
rollback journal is played back into the database file to
revert the database file to its original state.  The [COMMIT](lang_transaction.html) occurs
when the rollback journal is deleted.

The WAL approach inverts this.  The original content is preserved
in the database file and the changes are appended into a separate
WAL file.  A [COMMIT](lang_transaction.html) occurs when a special record indicating a commit
is appended to the WAL.  Thus a COMMIT can happen without ever writing
to the original database, which allows readers to continue operating
from the original unaltered database while changes are simultaneously being
committed into the WAL.  Multiple transactions can be appended to the
end of a single WAL file.

## 2.1. Checkpointing

Of course, one wants to eventually transfer all the transactions that
are appended in the WAL file back into the original database.  Moving
the WAL file transactions back into the database is called a
"*checkpoint*".

Another way to think about the difference between rollback and 
write-ahead log is that in the rollback-journal
approach, there are two primitive operations, reading and writing,
whereas with a write-ahead log
there are now three primitive operations:  reading, writing, and
checkpointing.

By default, SQLite does a checkpoint automatically when the WAL file
reaches a threshold size of 1000 pages.  (The
[SQLITE_DEFAULT_WAL_AUTOCHECKPOINT](compile.html#default_wal_autocheckpoint) compile-time option can be used to
specify a different default.) Applications using WAL do
not have to do anything in order for these checkpoints to occur.  
But if they want to, applications can adjust the automatic checkpoint
threshold.  Or they can turn off the automatic checkpoints and run 
checkpoints during idle moments or in a separate thread or process.

## 2.2. Concurrency

When a read operation begins on a WAL-mode database, it first
remembers the location of the last valid commit record in the WAL.
Call this point the "end mark".  Because the WAL can be growing and
adding new commit records while various readers connect to the database,
each reader can potentially have its own end mark.  But for any
particular reader, the end mark is unchanged for the duration of the
transaction, thus ensuring that a single read transaction only sees
the database content as it existed at a single point in time.

When a reader needs a page of content, it first checks the WAL to
see if that page appears there, and if so it pulls in the last copy
of the page that occurs in the WAL prior to the reader's end mark.
If no copy of the page exists in the WAL prior to the reader's end mark,
then the page is read from the original database file.  Readers can
exist in separate processes, so to avoid forcing every reader to scan
the entire WAL looking for pages (the WAL file can grow to
multiple megabytes, depending on how often checkpoints are run), a
data structure called the "wal-index" is maintained in shared memory
which helps readers locate pages in the WAL quickly and with a minimum
of I/O.  The wal-index greatly improves the performance of readers,
but the use of shared memory means that all readers must exist on the
same machine.  This is why the write-ahead log implementation will not
work on a network filesystem.

Writers merely append new content to the end of the WAL file.
Because writers do nothing that would interfere with the actions of
readers, writers and readers can run at the same time.  However,
since there is only one WAL file, there can only be one writer at
a time.

A checkpoint operation takes content from the WAL file
and transfers it back into the original database file.
A checkpoint can run concurrently with readers, however the checkpoint
must stop when it reaches a page in the WAL that is past the end mark
of any current reader.  The checkpoint has to stop at that point because
otherwise it might overwrite part of the database file that the reader
is actively using.  The checkpoint remembers (in the wal-index) how far
it got and will resume transferring content from the WAL to the database
from where it left off on the next invocation.

Thus a long-running read transaction can prevent a checkpointer from
making progress.  But presumably every read transaction will eventually
end and the checkpointer will be able to continue.

Whenever a write operation occurs, the writer checks how much progress
the checkpointer has made, and if the entire WAL has been transferred into
the database and synced and if no readers are making use of the WAL, then
the writer will rewind the WAL back to the beginning and start putting new
transactions at the beginning of the WAL.  This mechanism prevents a WAL
file from growing without bound.

## 2.3. Performance Considerations

Write transactions are very fast since they only involve writing
the content once (versus twice for rollback-journal transactions)
and because the writes are all sequential.  Further, syncing the
content to the disk is not required, as long as the application is
willing to sacrifice durability following a power loss or hard reboot.
(Writers sync the WAL on every transaction commit if
[PRAGMA synchronous](pragma.html#pragma_synchronous) is set to FULL but omit this sync if
[PRAGMA synchronous](pragma.html#pragma_synchronous) is set to NORMAL.)

On the other hand, read performance deteriorates as the WAL file
grows in size since each reader must check the WAL file for the content
and the time needed to check the WAL file is proportional
to the size of the WAL file.  The wal-index helps find content
in the WAL file much faster, but performance still falls off with
increasing WAL file size.  Hence, to maintain good read performance 
it is important to keep the WAL file size down by
running checkpoints at regular intervals.

Checkpointing does require sync operations in order to avoid
the possibility of database corruption following a power loss
or hard reboot.  The WAL must be synced to persistent storage
prior to moving content from the WAL into the database and the
database file must be synced prior to resetting the WAL.
Checkpoint also requires more seeking.
The checkpointer makes an effort to
do as many sequential page writes to the database as it can (the pages
are transferred from WAL to database in ascending order) but even
then there will typically be many seek operations interspersed among
the page writes.  These factors combine to make checkpoints slower than
write transactions.

The default strategy is to allow successive write transactions to
grow the WAL until the WAL becomes about 1000 pages in size, then to
run a checkpoint operation for each subsequent COMMIT until the WAL
is reset to be smaller than 1000 pages.  By default, the checkpoint will be
run automatically by the same thread that does the COMMIT that pushes
the WAL over its size limit.  This has the effect of causing most
COMMIT operations to be very fast but an occasional COMMIT (those that trigger
a checkpoint) to be much slower.  If that effect is undesirable, then
the application can disable automatic checkpointing and run the
periodic checkpoints in a separate thread, or separate process.
(Links to commands and interfaces to accomplish this are
[shown below](#how_to_checkpoint).)

Note that with [PRAGMA synchronous](pragma.html#pragma_synchronous) set to NORMAL, the checkpoint
is the only operation to issue an I/O barrier or sync operation
(fsync() on unix or FlushFileBuffers() on windows).  If an application
therefore runs checkpoint in a separate thread or process, the main
thread or process that is doing database queries and updates will never
block on a sync operation.  This helps to prevent "latch-up" in applications
running on a busy disk drive.  The downside to
this configuration is that transactions are no longer durable and
might rollback following a power failure or hard reset.

Notice too that there is a tradeoff between average read performance
and average write performance.  To maximize the read performance,
one wants to keep the WAL as small as possible and hence run checkpoints
frequently, perhaps as often as every COMMIT.  To maximize
write performance, one wants to amortize the cost of each checkpoint
over as many writes as possible, meaning that one wants to run checkpoints
infrequently and let the WAL grow as large as possible before each 
checkpoint.  The decision of how often to run checkpoints may therefore
vary from one application to another depending on the relative read
and write performance requirements of the application.
The default strategy is to run a checkpoint once the WAL
reaches 1000 pages and this strategy seems to work well in test applications on 
workstations, but other strategies might work better on different 
platforms or for different workloads.

# 3. Activating And Configuring WAL Mode

An SQLite database connection defaults to 
[journal_mode=DELETE](pragma.html#pragma_journal_mode).  To convert to WAL mode, use the
following pragma:

> 
PRAGMA journal_mode=WAL;

The journal_mode pragma returns a string which is the new journal mode.
On success, the pragma will return the string "wal".  If 
the conversion to WAL could not be completed (for example, if the [VFS](vfs.html)
does not support the necessary shared-memory primitives) then the
journaling mode will be unchanged and the string returned from the
primitive will be the prior journaling mode (for example "delete").

## 3.1. Automatic Checkpoint

By default, SQLite will automatically checkpoint whenever a [COMMIT](lang_transaction.html)
occurs that causes the WAL file to be 1000 pages or more in size, or when the 
last database connection on a database file closes.  The default 
configuration is intended to work well for most applications.
But programs that want more control can force a checkpoint
using the [wal_checkpoint pragma](pragma.html#pragma_wal_checkpoint) or 

... [Content truncated]