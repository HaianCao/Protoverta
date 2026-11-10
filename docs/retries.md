# Retries

```{include} compatibility/errors.md
:start-after: "## Error-class retry policy"
```

## Configuration

From the command line, pass a JSON object; from Python, a {class}`protoverta.error_classes.RetryPolicy`:

```console
protoverta ... --retry-policy '{"max_retries": 10, "delay_seconds": 60, "no_retry_classes": ["network"]}'
```

```python
from protoverta.error_classes import RetryPolicy
from protoverta.server import GatewayConfig

config = GatewayConfig(..., retry_policy=RetryPolicy(max_retries=10, delay_seconds=60), retry_log=print)
```

The launchers read the same settings from the `[retry]` table of their configuration file.
