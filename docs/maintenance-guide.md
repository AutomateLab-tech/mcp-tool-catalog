# MCP Tool Catalog maintenance guide

Inspect the attempted-server and successful-tool tables together. Failed probes are coverage evidence, not zero-tool servers. A recorded schema reflects the probed version and may differ from the current package.

## Installation and compatibility

Follow the repository README for the supported install method and runtime. Pin the version used in a reproducible example. Keep credentials in your client configuration, outside shared fixtures.

## Verify a change

Record the package or snapshot version, exact input, observation time and actual output. Check a materially different input and a valid neighboring case. Treat expected output as a fixture, not a live measurement.
