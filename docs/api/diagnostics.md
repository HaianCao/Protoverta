# Diagnostics

```{eval-rst}
.. automodule:: protoverta.observability
   :members: RequestObservation, jsonl_observer
```

`Observer` is any callable that accepts a `RequestObservation`; pass it as `observer=` to {func}`protoverta.server.serve` or {func}`protoverta.server.gateway_session`.
