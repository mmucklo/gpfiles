package main

import "sync/atomic"

// clientIDCounter allocates unique client IDs for ibkr_order subprocess connections.
//
// Reserved IDs:
//   1 = formerly used by Go engine's stub IBKRClient (retired)
//   2 = publisher (IBKR_PUBLISHER_CLIENT_ID env var)
//   3+ = ibkr_order subprocesses (allocated here)
//
// Each call to AllocateClientID() returns a monotonically increasing value
// starting at 3.  IBKR allows many simultaneous client connections; IDs are
// never reused within a process lifetime, so concurrent subprocesses are safe.
var clientIDCounter int32 = 2 // first Add(1) returns 3

// AllocateClientID returns a fresh, unique client ID for an ibkr_order subprocess.
func AllocateClientID() int {
	return int(atomic.AddInt32(&clientIDCounter, 1))
}
