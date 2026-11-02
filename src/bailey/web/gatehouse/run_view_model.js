/* `describe` and `status` in; what to draw out. No DOM, no network.
 *
 * The split is the specification's own (SPEC 4.4: limits live in `status`
 * "because they belong to the deployment and not to the agent"). A fact about
 * the agent is read from `describe`, a fact about the moment from `status`,
 * and nothing is inferred from one that the other states — so the level is
 * never guessed from a capability, and a credential's tick never comes from
 * the document that names it.
 *
 * Every rule the protocol writes as a MUST is here rather than in the
 * renderer, so it can be exercised without a page: an unrecognised input type
 * renders as text, an unrecognised validation member is ignored, and an
 * unrecognised output type is named rather than shown and never reported as a
 * failure.
 */
(function (global) {
  const root = global || (typeof window !== 'undefined' ? window : {});

  /* SPEC 4.1.1. A type outside this set is not a rejection — it renders as
   * text, because a client that refused would break on the first type a later
   * minor release adds. */
  const KNOWN_INPUT_TYPES = ['text', 'number', 'select'];
  /* The one input key the protocol reserves, and the reason a form has a large
   * box at all: it is the brief, filled in on every run. */
  const PROMPT_KEY = 'prompt';
  /* SPEC 4.1.4's two, and the one place an unknown may not simply be ignored. */
  const KNOWN_OUTPUT_TYPES = ['text', 'bytes'];
  /* The validation members this client understands. Anything else is ignored
   * rather than dropped from the document — the runner still enforces it, and
   * this form's checking is a courtesy, not a gate. */
  const KNOWN_VALIDATION = ['max_length', 'min', 'max', 'pattern', 'options'];

  /* The seller's presentation composition (`ui.json`). A third document
   * beside `describe` and `status`, and the only one a *seller* authors — so
   * every value it can carry is an enum or a string that renders as text, and
   * this client re-checks the palette rather than trusting the file it was
   * handed. It arrives from a bundle, and a bundle can come from any
   * distributor.
   *
   * **The version is read and never compared.** A first draft gated on the
   * document's version *family* and fell back whole when it did not match,
   * which reads as caution and is not: the family names a distributor, so the
   * gate refused another vendor's document rather than a shape this client
   * cannot draw — and it put that distributor's name in a package whose whole
   * rule is that it holds nothing about one. What actually protects this page
   * is the palette re-check below, which is vendor-neutral: a widget, a mode
   * or a template outside these sets is ignored whoever wrote it, and a member
   * this version has never heard of is ignored the ordinary additive way. */
  const UI_OUTPUT_MODES = ['text', 'markdown', 'file'];
  const UI_TEMPLATES = ['form-to-result'];
  /* Which widgets a declared type can honour. A hint may change the *shape* of
   * a control and never what the field means: the runner validates against
   * `describe`, so a seller who asked for a number box on a text field would
   * be styling a promise this client cannot keep. A hint outside its type's
   * row is reported rather than applied. */
  const UI_WIDGETS_BY_TYPE = { text: ['line', 'box'], number: ['number'], select: ['choice'] };

  /* Prefixes that identify a live provider key on sight, the same set the
   * deployed-runner probe scans for. SPEC 4.1.3 is a claim about the whole
   * payload rather than about one array, so this looks at the serialised
   * document and not only at `credentials[].value`. */
  const SECRET_PREFIXES = ['sk-', 'sk_live_', 'sk_test_', 'ghp_', 'github_pat_', 'xoxb-', 'AIza', 'AKIA'];
  /* A prefix counts only where a token starts and key characters follow it.
   * `sk-` is inside "risk-free", "task-list" and "Flask-based", which an MCP
   * server's tool descriptions say all the time, and a bare substring test
   * blanked the whole form over a description rather than over a key. */
  const SECRET_SHAPE = new RegExp(
    '(?:^|[^A-Za-z0-9_])(' +
      SECRET_PREFIXES.map(prefix => prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') +
      ')[A-Za-z0-9_-]{6,}'
  );

  function isObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function text(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  /* A link the page may draw: an absolute `http:` or `https:` address, and
   * nothing else. `signup_url` is a runner author's prose, and the schema's
   * `"format": "uri"` is satisfied by a `javascript:` URI as readily as by a
   * web page; an anchor is the one element through which a string becomes
   * something a click runs. Anything else draws no link. `host_blocks.js`
   * applies the same rule to the same member, and one case table holds both. */
  function webLink(value) {
    const candidate = text(value).trim();
    if (!candidate) return '';
    let parsed;
    try {
      parsed = new URL(candidate);
    } catch (err) {
      return '';
    }
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : '';
  }

  /* SPEC 4.1.3 and 7: a `describe` carrying a credential *value* is
   * nonconformant, and a client meeting one SHOULD refuse to proceed. This one
   * refuses — returning the reason rather than throwing, so the page can say
   * what is wrong instead of rendering nothing. */
  function credentialLeak(describe) {
    const declared = asArray(describe && describe.credentials);
    const named = declared.find(entry => isObject(entry) && 'value' in entry);
    if (named) {
      return 'This runner published a value for ' + text(named.env) +
        '. A credential value must never travel in `describe`, so nothing here will run against it.';
    }
    let serialised = '';
    try {
      serialised = JSON.stringify(describe || {});
    } catch (err) {
      return '';
    }
    const hit = SECRET_SHAPE.exec(serialised);
    if (hit) {
      return 'This runner’s description carries something shaped like a live API key (' + hit[1] +
        '…). A credential value must never travel in `describe`, so nothing here will run against it.';
    }
    return '';
  }

  /* The form, in declared order (SPEC 4.1.1 — `inputs` is an ordered array and
   * a client renders it in order). */
  /* `ui.json` in, a lookup table out. Nothing here can hide or reorder a
   * field: the form is still built by walking `describe.inputs` and asking
   * this for decoration, which is what makes "a required input the seller left
   * out is appended, never hidden" structural rather than a rule to remember. */
  function presentation(composition) {
    const doc = isObject(composition) ? composition : {};
    const output = isObject(doc.output) ? doc.output : {};
    const blocks = {};
    asArray(doc.inputs).filter(isObject).forEach(function (block) {
      const key = text(block.key);
      /* First mention wins, matching the server's own normaliser: two blocks
       * for one field is a document disagreeing with itself, and merging would
       * decide which half the seller meant. */
      if (!key || Object.prototype.hasOwnProperty.call(blocks, key)) return;
      blocks[key] = {
        widget: text(block.widget),
        help: text(block.help),
        placeholder: text(block.placeholder),
      };
    });
    const mode = text(output.mode);
    const template = text(doc.template);
    return {
      present: Boolean(text(doc.run_label)) || Boolean(mode) || Object.keys(blocks).length > 0,
      /* Carried as data the caller may want to show, never as something this
       * client decides on. */
      version: text(doc.version),
      template: UI_TEMPLATES.indexOf(template) !== -1 ? template : UI_TEMPLATES[0],
      /* The name the seller asked for, not the one that will be drawn — a
       * notice quoting the fallback would name the layout the page is already
       * showing, which reads as a bug rather than as the fallback it is. */
      unrecognisedTemplate: Boolean(template) && UI_TEMPLATES.indexOf(template) === -1 ? template : '',
      runLabel: text(doc.run_label),
      outputMode: UI_OUTPUT_MODES.indexOf(mode) !== -1 ? mode : UI_OUTPUT_MODES[0],
      blocks,
    };
  }

  /* A host may say that one `select` input chooses which of the others apply:
   * an MCP server's tool, where each tool takes arguments of its own. Nothing
   * in `describe` states that, so the fact arrives the way the composition
   * does, as a document handed in by whoever mounted the page:
   *
   *   { key, groups: { <option>: { inputs: [key…], required: [key…], about } } }
   *
   * Re-checked rather than trusted, and it cannot put a field out of reach: a
   * group that is not one of the select's options is ignored, so its inputs
   * stay on the page, and an input named by two groups belongs to the first
   * (the composition's own rule for two blocks naming one key). */
  function inputChoice(describe, choice) {
    const doc = isObject(choice) ? choice : {};
    const key = text(doc.key);
    const declared = asArray(describe && describe.inputs).filter(isObject);
    const chooser = declared.find(entry => text(entry.key) === key && text(entry.type) === 'select');
    const options = chooser && isObject(chooser.validation) ? asArray(chooser.validation.options).map(text) : [];
    if (!key || !options.length || !isObject(doc.groups)) return null;
    const keys = declared.map(entry => text(entry.key)).filter(name => name && name !== key);
    const groupOf = {};
    const required = {};
    const about = {};
    options.forEach(function (option) {
      if (!Object.prototype.hasOwnProperty.call(doc.groups, option) || !isObject(doc.groups[option])) return;
      const group = doc.groups[option];
      about[option] = text(group.about);
      asArray(group.inputs).map(text).forEach(function (name) {
        if (keys.indexOf(name) === -1 || Object.prototype.hasOwnProperty.call(groupOf, name)) return;
        groupOf[name] = option;
      });
      asArray(group.required).map(text).forEach(function (name) {
        if (groupOf[name] === option) required[name] = true;
      });
    });
    return { key, groupOf, required, about };
  }

  function formFields(describe, composition, choice) {
    const ui = composition && composition.blocks ? composition : presentation(composition);
    const plan = choice && choice.groupOf ? choice : inputChoice(describe, choice);
    return asArray(describe && describe.inputs)
      .filter(isObject)
      .map(function (declared) {
        const declaredType = text(declared.type);
        const known = KNOWN_INPUT_TYPES.indexOf(declaredType) !== -1;
        const validation = isObject(declared.validation) ? declared.validation : {};
        const options = asArray(validation.options).map(text);
        /* A `select` whose options never arrived cannot be a picker — the
         * schema calls `options` REQUIRED for that type, but a client that
         * rendered an empty dropdown would offer a field nobody can fill. */
        const type = known && !(declaredType === 'select' && !options.length) ? declaredType : 'text';
        const key = text(declared.key);
        const block = Object.prototype.hasOwnProperty.call(ui.blocks, key) ? ui.blocks[key] : null;
        const allowed = UI_WIDGETS_BY_TYPE[type] || [];
        const widget = block && allowed.indexOf(block.widget) !== -1 ? block.widget : '';
        /* Absent the seller's say-so, the reserved brief is the big box and
         * everything else is a line — which is exactly the guess this document
         * exists to replace, so a hint overrides it in both directions. */
        const multiline = widget ? widget === 'box' : key === PROMPT_KEY;
        /* The option this field belongs to, or '' for a field every run takes.
         * A choice can add a requirement and never remove one: the runner
         * enforces `describe`'s own either way. */
        const group = plan && Object.prototype.hasOwnProperty.call(plan.groupOf, key) ? plan.groupOf[key] : '';
        return {
          key,
          label: text(declared.label) || text(declared.key),
          type,
          /* Kept so the page can say it is showing a text box for a type it
           * did not recognise, rather than silently flattening the form. */
          declaredType,
          unrecognisedType: !known,
          group,
          required: declared.required === true || Boolean(group && plan.required[key]),
          defaultValue: declared.default === null || declared.default === undefined ? '' : declared.default,
          multiline,
          widget,
          help: block ? block.help : '',
          placeholder: block ? block.placeholder : '',
          /* Named rather than dropped, the same courtesy `ignoredValidation`
           * pays one line down: a hint this field's type cannot wear is a fact
           * the seller previewing the page is entitled to see. */
          ignoredWidget: block && block.widget && !widget ? block.widget : '',
          options,
          maxLength: typeof validation.max_length === 'number' ? validation.max_length : null,
          min: typeof validation.min === 'number' ? validation.min : null,
          max: typeof validation.max === 'number' ? validation.max : null,
          pattern: typeof validation.pattern === 'string' ? validation.pattern : '',
          /* Named, not applied. Reporting them is what makes "ignored" a
           * decision somebody can see rather than an omission. */
          ignoredValidation: Object.keys(validation).filter(name => KNOWN_VALIDATION.indexOf(name) === -1),
        };
      })
      .filter(field => field.key !== '');
  }

  /* What a run sends: every field no choice governs, and the fields of the
   * option chosen. Another option's fields stay home, because the runner
   * checks every declared input it is handed, and a stale value in a field the
   * buyer cannot see would refuse the run over something they cannot fix.
   *
   * A control holds text, and SPEC 4.1.1's `number` is a JSON number, so a
   * number field's text is sent as the number it spells. Text that spells none
   * is sent as typed, for the runner to refuse by name; an empty one stays
   * empty, which the runner reads as not given. */
  function runInputs(fields, values, choice) {
    const chosen = choice ? text(values[choice.key]) : '';
    const inputs = {};
    asArray(fields).forEach(function (field) {
      if (field.group && field.group !== chosen) return;
      if (!Object.prototype.hasOwnProperty.call(values, field.key)) return;
      const value = values[field.key];
      const spelled = field.type === 'number' && text(value).trim() !== '' ? Number(value) : NaN;
      inputs[field.key] = Number.isFinite(spelled) ? spelled : value;
    });
    return inputs;
  }

  /* The values a form starts with: an example's inputs where there is one,
   * each field's own default otherwise. SPEC 4.1 offers no template format and
   * needs none — the examples the seller captured are the templates. */
  function initialValues(describe, exampleIndex, composition) {
    const fields = formFields(describe, composition);
    const examples = asArray(describe && describe.examples).filter(isObject);
    const chosen = examples[exampleIndex === undefined ? 0 : exampleIndex];
    const fromExample = chosen && isObject(chosen.inputs) ? chosen.inputs : null;
    const values = {};
    fields.forEach(function (field) {
      if (fromExample && Object.prototype.hasOwnProperty.call(fromExample, field.key)) {
        values[field.key] = text(fromExample[field.key]);
      } else {
        values[field.key] = text(field.defaultValue);
      }
    });
    return values;
  }

  function examples(describe) {
    return asArray(describe && describe.examples)
      .filter(isObject)
      .map(function (entry, index) {
        const inputs = isObject(entry.inputs) ? entry.inputs : {};
        const first = Object.keys(inputs)[0];
        return {
          index,
          inputs,
          output: text(entry.output),
          /* A chip needs something to say. The first input's value is what a
           * seller would recognise; the ordinal is the fallback so a chip is
           * never blank. */
          label: first && text(inputs[first]) ? text(inputs[first]) : 'Example ' + (index + 1),
        };
      });
  }

  /* SPEC 4.1.4. The third arm is the point: an unrecognised type is neither
   * shown as text nor treated as a failure. */
  function outputPlan(describe, composition) {
    const ui = composition && composition.blocks ? composition : presentation(composition);
    const declared = isObject(describe && describe.output) ? describe.output : {};
    const type = text(declared.type) || 'text';
    /* The seller's mode is a *presentation* choice over text the runner
     * returns, so it applies to `text` and nothing else. `bytes` is already a
     * download by protocol (SPEC 4.1.4 forbids showing it), and an
     * unrecognised type is named rather than drawn — neither is a surface a
     * composition may repaint. */
    const mode = type === 'text' ? ui.outputMode : 'text';
    if (KNOWN_OUTPUT_TYPES.indexOf(type) !== -1) {
      return { type, mode, unrecognised: false, example: text(declared.example), notice: '' };
    }
    return {
      type,
      mode: 'text',
      unrecognised: true,
      example: text(declared.example),
      notice: 'This agent returns ' + type + ', which this client cannot show. The run itself was fine.',
    };
  }

  /* Markdown, in the only form a renderer that forbids `innerHTML` can honour:
   * block structure, built from `createElement` and `textContent`.
   *
   * Headings, paragraphs, bullet and numbered lists and fenced code are
   * recognised; **inline markers are left exactly as the agent typed them**.
   * That is a deliberate stop rather than an unfinished parser — inline
   * markdown's useful half is the link, and an anchor whose `href` comes from
   * agent output is precisely the escape hatch `run_renderer.js` is written to
   * make structurally impossible. A seller choosing this mode is choosing
   * structure, and the page says so.
   *
   * The parse lives here rather than in the renderer for the same reason every
   * protocol rule does: it can be exercised without a page. */
  function outputBlocks(value) {
    const lines = String(value === undefined || value === null ? '' : value).split(/\r?\n/);
    const blocks = [];
    let paragraph = [];
    let list = null;
    let code = null;

    function flushParagraph() {
      if (paragraph.length) blocks.push({ kind: 'paragraph', text: paragraph.join(' ') });
      paragraph = [];
    }
    function flushList() {
      if (list) blocks.push(list);
      list = null;
    }

    lines.forEach(function (line) {
      if (code !== null) {
        if (/^\s*```/.test(line)) {
          blocks.push({ kind: 'code', text: code.join('\n') });
          code = null;
        } else {
          code.push(line);
        }
        return;
      }
      if (/^\s*```/.test(line)) {
        flushParagraph();
        flushList();
        code = [];
        return;
      }
      const heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        flushParagraph();
        flushList();
        blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() });
        return;
      }
      const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
      const ordered = bullet ? null : /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (bullet || ordered) {
        flushParagraph();
        const isOrdered = Boolean(ordered);
        if (!list || list.ordered !== isOrdered) {
          flushList();
          list = { kind: 'list', ordered: isOrdered, items: [] };
        }
        list.items.push((bullet ? bullet[1] : ordered[1]).trim());
        return;
      }
      if (!line.trim()) {
        flushParagraph();
        flushList();
        return;
      }
      paragraph.push(line.trim());
    });

    /* An unterminated fence still yields its block. Refusing it would drop the
     * whole result over a missing three characters at the end of a stream. */
    if (code !== null) blocks.push({ kind: 'code', text: code.join('\n') });
    flushParagraph();
    flushList();
    return blocks;
  }

  /* SPEC 4.1.2: a client SHOULD surface `write_tools` and MAY require a
   * confirmation. This one requires it, once per session, because the tools
   * named here are the ones that spend money or reach past the workspace. */
  /* How much of a conversation a reply carries when the host names no bound
   * and the brief declares no `max_length`: about four thousand tokens of
   * text, enough for several exchanges and a cap on what a long one costs. */
  const CONVERSATION_MAX_CHARS = 16000;

  /* A reply in a conversation, as the one brief a run takes. No verb takes a
   * `run_id` (SPEC 4.5), so a client cannot point a run at an earlier one, and
   * the way an agent sees what was said before is for the client to send it.
   * The earlier turns come first, oldest first, then the new message, each
   * under a line saying whose words they are, in plain text the agent reads as
   * its brief.
   *
   * `limit` bounds the whole in characters. The oldest turns are the ones
   * left out when it would pass it, and the answer says how many were, so the
   * page can say so rather than let a reader think the agent saw them. The new
   * message is never cut: the field's own `max_length` bounds it as typed. */
  function conversationBrief(turns, message, limit) {
    const said = text(message);
    const all = asArray(turns).filter(isObject);
    const cap = typeof limit === 'number' && limit > 0 ? limit : CONVERSATION_MAX_CHARS;
    for (let first = 0; first < all.length; first++) {
      const kept = all.slice(first);
      const parts = ['This continues an earlier exchange with you. What was said before, oldest first:'];
      kept.forEach(function (turn) {
        parts.push('The user wrote:\n' + text(turn.brief));
        parts.push('You answered:\n' + text(turn.answer));
      });
      parts.push('Now the user writes, and this is the message to answer:\n' + said);
      const value = parts.join('\n\n');
      if (value.length <= cap) return { value, sent: kept.length, dropped: first };
    }
    return { value: said, sent: 0, dropped: all.length };
  }

  function capabilities(describe) {
    const declared = isObject(describe && describe.capabilities) ? describe.capabilities : {};
    const writeTools = asArray(declared.write_tools).map(text).filter(Boolean);
    return {
      tools: asArray(declared.tools).map(text).filter(Boolean),
      writeTools,
      needsWriteConfirmation: writeTools.length > 0,
      /* Absent and false are read identically (SPEC 4.2), so Retry warns
       * unless the runner has said a replay is free. */
      idempotentRetry: declared.idempotent_retry === true,
    };
  }

  /* `describe` names the credentials; `status` says which are set. Neither
   * document answers alone, which is why this takes both. */
  function readiness(describe, status) {
    const declared = asArray(describe && describe.credentials).filter(isObject);
    const reported = isObject(status && status.credentials) ? status.credentials : {};
    const missing = asArray(reported.missing).map(text);
    const rows = declared.map(function (entry) {
      const env = text(entry.env);
      return {
        env,
        purpose: text(entry.purpose),
        signupUrl: webLink(entry.signup_url),
        satisfied: missing.indexOf(env) === -1,
      };
    });
    /* A name `status` reports missing that `describe` never declared still has
     * to appear, or the list contradicts the runner it is describing. */
    missing.forEach(function (env) {
      if (!rows.some(row => row.env === env)) {
        rows.push({ env, purpose: '', signupUrl: '', satisfied: false });
      }
    });
    return {
      rows,
      satisfied: reported.satisfied === true,
      /* Said once, beside the list: this is not a field on this page. */
      where: 'These go in the runner’s own .env file, not here.',
    };
  }

  /* SPEC 2.2 as the shell reads it, and the one case that is not about a
   * lapsed licence: `unknown` with no `checked_at` is a runner that has never
   * confirmed anything and will answer 503 to a run — worth saying before the
   * buyer clicks rather than after. */
  function entitlementBanner(status) {
    const entitlement = isObject(status && status.entitlement) ? status.entitlement : {};
    const state = text(entitlement.state);
    const checkedAt = text(entitlement.checked_at);
    if (state === 'revoked') {
      return {
        tone: 'blocked',
        state,
        message:
          'Your access to this agent has ended or could not be confirmed. ' +
          'Check with the place you bought it.',
      };
    }
    if (state === 'unknown' && !checkedAt) {
      return {
        tone: 'blocked',
        state,
        message:
          'This runner has never confirmed its licence, so a run will not start. ' +
          'It needs to reach its distributor once.',
      };
    }
    if (state === 'unknown') {
      const grace = typeof entitlement.grace_seconds === 'number' ? entitlement.grace_seconds : null;
      return {
        tone: 'caution',
        state,
        checkedAt,
        graceSeconds: grace,
        message:
          'This runner is running through an outage. Its licence was last confirmed at ' +
          checkedAt + (grace === null ? '.' : ', and access is guaranteed for ' + grace + 's from then.'),
      };
    }
    if (state === 'active' || state === 'not_required') {
      return {
        tone: 'quiet',
        state,
        checkedAt,
        message: checkedAt ? 'Licence confirmed at ' + checkedAt + '.' : '',
      };
    }
    /* A state this client has not heard of is reported rather than guessed
     * at — the same posture every other unknown in this protocol gets. */
    return { tone: 'quiet', state, message: state ? 'Entitlement state: ' + state + '.' : '' };
  }

  /* SPEC 3: the shell MUST NOT assume a level it has not read from `status`. */
  function levelPlan(status) {
    const level = typeof (status && status.level) === 'number' ? status.level : 0;
    if (level >= 3) return { level, canRun: true, streams: true, note: '' };
    if (level === 2) {
      return {
        level,
        canRun: true,
        streams: false,
        note: 'This runner returns the whole result at once rather than streaming it.',
      };
    }
    if (level === 1) {
      return {
        level,
        canRun: false,
        streams: false,
        note: 'This runner describes the agent but cannot run it.',
      };
    }
    return {
      level,
      canRun: false,
      streams: false,
      note: 'This runner did not report a conformance level, so nothing here will try to run it.',
    };
  }

  /* SPEC 4.4: absent means the runner imposes no such bound, and the shell
   * says nothing rather than inventing a default. */
  function limitsView(status) {
    const limits = isObject(status && status.limits) ? status.limits : {};
    const number = name => (typeof limits[name] === 'number' ? limits[name] : null);
    return {
      maxRunSeconds: number('max_run_seconds'),
      maxConcurrentRuns: number('max_concurrent_runs'),
      maxOutputBytes: number('max_output_bytes'),
      idempotencyRetentionSeconds: number('idempotency_retention_seconds'),
    };
  }

  /* Not in the specification — one distributor's runner adds it. Treated
   * as any unknown member: ignored when absent, a courtesy line when present.
   * It must never become a requirement, or this stops being a Postern client. */
  function updateNotice(status) {
    const update = isObject(status && status.update) ? status.update : null;
    if (!update || text(update.state) !== 'update_available') return '';
    return 'An update is available (' + text(update.current) + ' → ' + text(update.latest) +
      '). Re-pull to update.';
  }

  function header(describe) {
    const agent = isObject(describe && describe.agent) ? describe.agent : {};
    return {
      /* Shown verbatim: it is what the buyer puts in `.env` and what the
       * runner prints at boot, so one identifier keeps one spelling. */
      id: text(agent.id),
      name: text(agent.name),
      version: text(agent.version),
      summary: text(agent.summary),
    };
  }

  function build(describe, status, composition, choice) {
    const ui = presentation(composition);
    const plan = inputChoice(describe, choice);
    return {
      refuseReason: credentialLeak(describe),
      header: header(describe),
      fields: formFields(describe, ui, plan),
      choice: plan,
      initialValues: initialValues(describe, undefined, ui),
      examples: examples(describe),
      output: outputPlan(describe, ui),
      presentation: ui,
      capabilities: capabilities(describe),
      readiness: readiness(describe, status),
      entitlement: entitlementBanner(status),
      level: levelPlan(status),
      limits: limitsView(status),
      updateNotice: updateNotice(status),
      state: text(status && status.state),
    };
  }

  root.Gatehouse = Object.assign({}, root.Gatehouse, {
    viewModel: {
      PROMPT_KEY,
      KNOWN_INPUT_TYPES,
      KNOWN_OUTPUT_TYPES,
      UI_OUTPUT_MODES,
      UI_TEMPLATES,
      UI_WIDGETS_BY_TYPE,
      build,
      presentation,
      outputBlocks,
      header,
      inputChoice,
      formFields,
      runInputs,
      initialValues,
      conversationBrief,
      CONVERSATION_MAX_CHARS,
      examples,
      outputPlan,
      capabilities,
      readiness,
      entitlementBanner,
      levelPlan,
      limitsView,
      updateNotice,
      credentialLeak,
      webLink,
    },
  });

  /* `SigrixRun` is the name this package was born under, inside one host. It
   * is kept pointing at the same object so that host keeps working across the
   * extraction, and because a global is the one part of a browser package a
   * consumer cannot alias for itself. Deprecated; removed at 1.0. */
  root.SigrixRun = root.Gatehouse;
})(typeof window !== 'undefined' ? window : globalThis);
