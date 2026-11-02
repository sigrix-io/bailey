/* What to add to a refusal the runner already worded.
 *
 * `error.message` is shown verbatim in every case — SPEC 2.1 says it SHOULD be
 * safe to show a user, and rewriting it here would replace what the runner
 * knows with what this client guessed. What this adds is only the next move,
 * which is the part a message written by a runner cannot supply: it does not
 * know what the page is offering.
 *
 * The unknown arm is a MUST rather than a courtesy. A client that recognised
 * only the codes in the table would turn every code added in a later minor
 * release into a breaking change for its own users, so an unrecognised code
 * degrades to its HTTP status class and still shows the message.
 */
(function (global) {
  const root = global || (typeof window !== 'undefined' ? window : {});

  function text(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  /* `context` carries what the page knows and the refusal does not: the
   * credentials `describe` declared, the level `status` reported, the state it
   * was in when the request went out, and the run id if one had been issued. */
  function guidance(refusal, context) {
    const ctx = context || {};
    const status = refusal && typeof refusal.status === 'number' ? refusal.status : 0;
    const code = text(refusal && refusal.code);
    const detail = refusal && refusal.detail ? refusal.detail : {};

    if (code === 'bad_request') {
      return {
        nextMove: 'Check the highlighted field and try again.',
        /* The runner names the offending key in `detail.key` where it can, so
         * the form can point at it rather than making the buyer re-read a
         * sentence to find out which field it meant. */
        highlightKey: text(detail && detail.key),
        canRetry: false,
      };
    }

    if (code === 'missing_credential') {
      const names = Array.isArray(detail && detail.missing) ? detail.missing.map(text) : [];
      const declared = Array.isArray(ctx.credentials) ? ctx.credentials : [];
      const rows = names.map(function (env) {
        const known = declared.find(entry => entry && entry.env === env) || {};
        return { env, purpose: text(known.purpose), signupUrl: text(known.signupUrl) };
      });
      return {
        nextMove: 'Add it to the runner’s own .env file and start the runner again — it is not a field on this page.',
        credentials: rows,
        canRetry: false,
      };
    }

    if (code === 'not_entitled') {
      return {
        /* The same sentence the entitlement banner uses, and for the same
         * reason: SPEC 5.5 does not let the runner tell a refund from a
         * withdrawal from a mistyped token, so neither may this. */
        nextMove: 'Your access to this agent has ended or could not be confirmed. Check with the place you bought it.',
        canRetry: false,
      };
    }

    if (code === 'unavailable') {
      const inFlight = text(ctx.state) === 'running' ||
        (typeof ctx.maxConcurrentRuns === 'number' && ctx.maxConcurrentRuns === 1 && ctx.runInFlight === true);
      return {
        nextMove: inFlight
          ? 'Another run is in flight on this runner. Wait for it to finish and try again.'
          : 'The runner is not ready yet. Try again in a moment.',
        canRetry: true,
      };
    }

    if (code === 'run_timeout') {
      const bound = detail && typeof detail.max_run_seconds === 'number' ? detail.max_run_seconds : null;
      return {
        nextMove:
          (bound === null ? 'This run passed the runner’s limit and was stopped.' :
            'This run passed the runner’s limit of ' + bound + ' seconds and was stopped.') +
          ' Try again with less to do, or use a runner started with a longer limit.',
        canRetry: true,
      };
    }

    if (code === 'not_implemented') {
      const level = typeof ctx.level === 'number' ? ctx.level : null;
      return {
        nextMove:
          (level === null ? 'This runner does not implement that verb.' :
            'This runner reports conformance level ' + level + ', which does not include that verb.') +
          ' Retrying will not help.',
        canRetry: false,
      };
    }

    if (code === 'agent_error') {
      const runId = text(ctx.runId);
      return {
        nextMove: 'The agent ran and failed.' + (runId ? ' Quote run ' + runId + ' when reporting it.' : ''),
        canRetry: true,
      };
    }

    if (code === 'unauthorized') {
      /* On a hosted run the runner that refused is the platform's own proxy,
       * and what failed is the buyer's sign-in. Its message says what
       * to do, and "whoever deployed it" would be the platform. */
      if (ctx.hosted === true) {
        return { nextMove: '', canRetry: false };
      }
      /* Added to SPEC 2.1 alongside the runner's own inbound authentication:
       * a runner bound off-machine requires a credential this page did not
       * present. Said in terms of the runner rather than of any one host,
       * because the scheme is the deployment's and not the protocol's. */
      return {
        nextMove: 'This runner requires an authorization token that was not presented. Whoever deployed it can supply one.',
        canRetry: false,
      };
    }

    /* Unrecognised: the status class, and nothing invented. Note a runner has
     * no `withdrawn` and no distributor `not_found` to give (SPEC 2.1 marks
     * both distributor-side), so there is deliberately no copy for them — one
     * arriving here lands in this arm, which is the honest answer. */
    if (status >= 500) {
      return { nextMove: 'The runner reported a failure. Trying again may help.', canRetry: true };
    }
    if (status >= 400) {
      return { nextMove: 'The runner refused this request, and nothing ran.', canRetry: false };
    }
    return { nextMove: '', canRetry: false };
  }

  root.Gatehouse = Object.assign({}, root.Gatehouse, { refusalGuidance: guidance });

  /* `SigrixRun` is the name this package was born under, inside one host. It
   * is kept pointing at the same object so that host keeps working across the
   * extraction, and because a global is the one part of a browser package a
   * consumer cannot alias for itself. Deprecated; removed at 1.0. */
  root.SigrixRun = root.Gatehouse;
})(typeof window !== 'undefined' ? window : globalThis);
