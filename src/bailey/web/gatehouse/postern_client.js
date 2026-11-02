/* The four verbs over `fetch`, and nothing else.
 *
 * Holds no knowledge of any marketplace: the base URL is a constructor
 * argument, so the same client serves a proxy on this origin and a runner on
 * loopback. Every request it makes goes to that base and to the protocol's own
 * path prefix — a test asserts exactly that, because a font, an icon CDN or a
 * "check for updates" call would each be a second origin this page reaches.
 *
 * **`stream` is read with a body reader, never `EventSource`.** That API
 * cannot POST, and it reconnects on its own — which SPEC 4.5 names as the way
 * a client spends someone's money twice, since a reopened stream is a new run.
 * There is deliberately no retry loop around the reader either.
 *
 * **A disconnect is a cancel** (SPEC 4.5), so Stop is an `AbortController` and
 * not a verb: the protocol has four and will not grow a fifth.
 */
(function (global) {
  const root = global || (typeof window !== 'undefined' ? window : {});

  const PATH_PREFIX = '/postern/v0';
  const JSON_MEDIA_TYPE = 'application/json';

  /* A refusal the runner stated in SPEC 2.1's envelope, carried whole so the
   * caller can show `message` verbatim and add its own next move. `code` may
   * be one this client has never heard of: 2.1 requires a client to treat an
   * unrecognised code as a generic failure of its status class rather than
   * reject the response, so nothing here validates it against a list. */
  class PosternRefusal extends Error {
    constructor(status, code, message, detail) {
      super(message || 'The runner refused this request.');
      this.name = 'PosternRefusal';
      this.status = status;
      this.code = code || '';
      this.detail = detail === undefined ? null : detail;
    }
  }

  /* A failure that is not a refusal: the runner was unreachable, or answered
   * something that is not the envelope. Kept distinct because the two want
   * different copy — one is the agent's operator to fix, the other is the
   * network. */
  class PosternUnreachable extends Error {
    constructor(message, cause) {
      super(message);
      this.name = 'PosternUnreachable';
      this.cause = cause || null;
    }
  }

  function joinUrl(baseUrl, verb) {
    return String(baseUrl).replace(/\/+$/, '') + PATH_PREFIX + verb;
  }

  async function readEnvelope(response) {
    /* A non-2xx body is the envelope (SPEC 2.1) — including for paths and
     * methods the runner does not implement. A body that is not JSON means
     * something other than a runner is answering this URL, which is worth
     * saying rather than reporting as an empty refusal. */
    let payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      throw new PosternUnreachable(
        'The runner answered ' + response.status + ' with something that is not JSON. ' +
          'Check that this address is a runner and not a proxy or sign-in page.',
        err
      );
    }
    const error = payload && typeof payload === 'object' ? payload.error : null;
    if (!error || typeof error !== 'object') {
      throw new PosternUnreachable(
        'The runner answered ' + response.status + ' without the error envelope this protocol requires.'
      );
    }
    return new PosternRefusal(response.status, error.code, error.message, error.detail);
  }

  /* One SSE frame per `\n\n`, each carrying an `event:` name and a `data:`
   * JSON line. Written as a stateful splitter rather than a per-chunk parse
   * because a frame can arrive split across reads — the case that only shows
   * up against a real network and never against a stubbed one. */
  function createFrameParser() {
    let buffer = '';
    return function push(chunk) {
      buffer += chunk;
      const frames = [];
      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(raw);
        if (frame) frames.push(frame);
        boundary = buffer.indexOf('\n\n');
      }
      return frames;
    };
  }

  function parseFrame(raw) {
    let name = '';
    const dataLines = [];
    raw.split('\n').forEach(line => {
      if (line.startsWith('event:')) name = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      /* A comment line (`:`) is a keep-alive. A proxy sends them to hold the
       * connection open through an idle-timeout, so they are expected traffic
       * rather than a malformed frame. */
    });
    if (!name || !dataLines.length) return null;
    try {
      return { name, payload: JSON.parse(dataLines.join('\n')) };
    } catch (err) {
      /* A frame this client cannot parse is skipped rather than thrown: the
       * stream owes exactly one ending, and an unreadable frame in the middle
       * is not it. */
      return null;
    }
  }

  class PosternClient {
    constructor(baseUrl, options) {
      if (!baseUrl) throw new Error('PosternClient needs a base URL.');
      const settings = options || {};
      this.baseUrl = String(baseUrl).replace(/\/+$/, '');
      this._fetch = settings.fetchImpl || (root.fetch ? root.fetch.bind(root) : null);
      /* Extra headers the embedder needs — a proxy's CSRF token, say. The
       * client sets none of its own beyond the media type the protocol
       * requires, because a header it invented would be one more thing a
       * runner has to be configured to admit through its preflight. */
      this._headers = settings.headers || {};
      if (!this._fetch) throw new Error('PosternClient needs a fetch implementation.');
    }

    url(verb) {
      return joinUrl(this.baseUrl, verb);
    }

    async _get(verb) {
      let response;
      try {
        response = await this._fetch(this.url(verb), {
          method: 'GET',
          headers: Object.assign({ Accept: JSON_MEDIA_TYPE }, this._headers),
        });
      } catch (err) {
        throw new PosternUnreachable('Could not reach the runner at ' + this.baseUrl + '.', err);
      }
      if (!response.ok) throw await readEnvelope(response);
      return response.json();
    }

    describe() {
      return this._get('/describe');
    }

    status() {
      return this._get('/status');
    }

    _postInit(inputs, options) {
      const settings = options || {};
      const headers = Object.assign(
        { 'Content-Type': JSON_MEDIA_TYPE, Accept: JSON_MEDIA_TYPE },
        this._headers
      );
      /* SPEC 4.2: the same key with the same inputs is a replay; with
       * different inputs it is a 409. The caller owns that decision, so the
       * key is only sent when it hands one over. */
      if (settings.idempotencyKey) headers['Idempotency-Key'] = settings.idempotencyKey;
      return {
        method: 'POST',
        headers,
        /* The media type above is what makes a browser preflight this request
         * (SPEC 2.3). A runner refuses any other, before reading the body. */
        body: JSON.stringify({ inputs: inputs || {} }),
        signal: settings.signal,
      };
    }

    async run(inputs, options) {
      let response;
      try {
        response = await this._fetch(this.url('/run'), this._postInit(inputs, options));
      } catch (err) {
        if (err && err.name === 'AbortError') throw err;
        throw new PosternUnreachable('Could not reach the runner at ' + this.baseUrl + '.', err);
      }
      if (!response.ok) throw await readEnvelope(response);
      return response.json();
    }

    /* Yields each event to `onEvent` as it arrives. Resolves when the stream
     * ends; rejects on a refusal raised before the first byte, which is the
     * only point a status code is still available — past it SPEC 4.5 requires
     * the failure to arrive as an `error` event instead, and this returns
     * normally so the caller renders that event rather than a thrown one. */
    async stream(inputs, options) {
      const settings = options || {};
      const onEvent = settings.onEvent || function () {};
      let response;
      try {
        response = await this._fetch(this.url('/stream'), this._postInit(inputs, settings));
      } catch (err) {
        if (err && err.name === 'AbortError') throw err;
        throw new PosternUnreachable('Could not reach the runner at ' + this.baseUrl + '.', err);
      }
      if (!response.ok) throw await readEnvelope(response);
      if (!response.body || typeof response.body.getReader !== 'function') {
        throw new PosternUnreachable('This browser cannot read a streamed response body.');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const push = createFrameParser();
      try {
        for (;;) {
          const chunk = await reader.read();
          if (chunk.done) break;
          push(decoder.decode(chunk.value, { stream: true })).forEach(onEvent);
        }
        /* Whatever the last read left mid-buffer. A conforming runner ends on
         * a frame boundary, so this is ordinarily empty. */
        push('\n\n').forEach(onEvent);
      } finally {
        /* Releasing the lock lets an abort actually close the socket, which is
         * what makes Stop a cancel rather than a client that stopped looking. */
        try {
          reader.releaseLock();
        } catch (err) {
          /* Already released by the abort. */
        }
      }
    }
  }

  root.Gatehouse = Object.assign({}, root.Gatehouse, {
    PATH_PREFIX,
    PosternClient,
    PosternRefusal,
    PosternUnreachable,
  });

  /* `SigrixRun` is the name this package was born under, inside one host. It
   * is kept pointing at the same object so that host keeps working across the
   * extraction, and because a global is the one part of a browser package a
   * consumer cannot alias for itself. Deprecated; removed at 1.0. */
  root.SigrixRun = root.Gatehouse;
})(typeof window !== 'undefined' ? window : globalThis);
