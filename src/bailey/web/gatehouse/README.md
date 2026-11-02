# Gatehouse, vendored

A copy of a release of [`@sigrix-io/gatehouse`](https://www.npmjs.com/package/@sigrix-io/gatehouse),
the browser client that draws an app's run page: the form from the runner's `describe`, the
steps as they happen, and the result. `RELEASE.json` names the release and holds each file's
sha256, which `tests/test_gatehouse_vendored.py` checks.

**Never edit these files here.** A change belongs in
[sigrix-io/gatehouse](https://github.com/sigrix-io/gatehouse); a release comes back with

```sh
python scripts/vendor_gatehouse.py 0.4.0
```

Bailey's page loads the four scripts in this order, then `gatehouse.css`:
`postern_client.js`, `run_view_model.js`, `refusal_copy.js`, `run_renderer.js`. They ship under
the licence and notice beside them.
