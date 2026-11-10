# Gateway

```{eval-rst}
.. autoclass:: protoverta.server.GatewayConfig
   :members: upstream_url
.. autofunction:: protoverta.server.gateway_session
.. autofunction:: protoverta.server.serve
.. autofunction:: protoverta.server.make_handler
.. autoclass:: protoverta.admission.SharedAdmission
   :members: acquire, release, cooldown, close
.. autoexception:: protoverta.admission.AdmissionDenied
.. autoexception:: protoverta.admission.AdmissionPolicyError
```
