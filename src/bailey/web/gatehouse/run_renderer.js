/* The page: header, readiness, templates, form, controls, output, usage.
 *
 * **Nothing here uses `innerHTML`.** Every string that came from the agent or
 * from its runner reaches the page through `textContent`, so "agent output is
 * rendered as text, never as markup" is a property of the file rather than a
 * rule somebody has to keep remembering — a source scan holds the line. This
 * repository has paid once already for an escaper that did not escape quotes;
 * the cheapest way not to pay again is to have nothing to escape.
 *
 * The base URL is a constructor argument and no listing identifier reaches
 * this file, so the same renderer serves a proxied runner and a local one.
 */
(function (global) {
  const root = global || (typeof window !== 'undefined' ? window : {});
  const NS = root.Gatehouse || {};

  function el(tag, className, textContent) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent !== undefined && textContent !== null) node.textContent = String(textContent);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /* `options.conversation`: `true`, or `{ maxChars }` to bound what a reply
   * carries. Anything else is no conversation: the one-run page this file has
   * always drawn. */
  function conversationSettings(option) {
    if (option === true) return { maxChars: null };
    if (option && typeof option === 'object') {
      return { maxChars: typeof option.maxChars === 'number' && option.maxChars > 0 ? option.maxChars : null };
    }
    return null;
  }

  function randomKey() {
    /* An idempotency key only has to be unique to this client (SPEC 4.2). */
    if (root.crypto && typeof root.crypto.randomUUID === 'function') return root.crypto.randomUUID();
    return 'k-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  }

  /* How many renderers this copy of the file has made. An id is unique to the
   * document, not to the container it is drawn in, so two renderers on one
   * page that both wrote `run-field-prompt` would bind each `<label>` to the
   * first page's field. Each renderer writes its ids under a scope of its own
   * instead, numbered in the order they were made. */
  let renderersMade = 0;

  class RunRenderer {
    /* `container` is where the page is drawn; `client` is any object with the
     * four verbs, so a caller may pass its own. `options.composition` is the
     * seller's `ui.json` — a *document*, handed in by whoever read the
     * bundle, because reading a file is not one of the four verbs and a
     * renderer that fetched it would be reaching for a fifth.
     * `options.hosted` says the platform operates this runner rather than the
     * person reading, which changes only what a failure may quote back (see
     * `reportFailure`). `options.choice` says one select input chooses which
     * of the others apply (`inputChoice` in the view model has its shape): a
     * fact the host read about this runner, handed in for the composition's
     * reason. `options.conversation` keeps each brief and answer on the page
     * and sends them with the next brief (`renderConversation`). Nothing else
     * is read. */
    constructor(container, client, options) {
      const settings = options || {};
      this.container = container;
      this.client = client;
      this.composition = settings.composition || null;
      this.choice = settings.choice || null;
      /* Who operates the runner this page is driving. Self-hosted is the
       * default because that is the deployment this file was written for, and
       * the hosted page states it rather than letting the presence of a proxy
       * base stand in for the statement — a proxy base is a coincidence of
       * that deployment, not a claim about who owns the keys. */
      this.hosted = settings.hosted === true;
      this.viewModel = null;
      this.values = {};
      this.writeToolsAccepted = false;
      this.abortController = null;
      this.lastRunId = '';
      this.lastInputsKey = '';
      this.idempotencyKey = '';
      this.onRunStateChange = settings.onRunStateChange || function () {};
      this.conversation = conversationSettings(settings.conversation);
      /* Held here and nowhere else: the page forgets the conversation when it
       * is left, and a runner is only ever sent it inside a brief. */
      this.turns = [];
      this.lastDropped = 0;
      this.lastAnswer = null;
      this.nodes = {};
      renderersMade += 1;
      this.idScope = 'run-' + renderersMade;
    }

    /* An id inside this renderer's scope. A host looking for a field finds it
     * by its `name`, which is the input's key, inside the container it handed
     * in; the id is this renderer's own business. */
    scopedId(part) {
      return this.idScope + '-' + part;
    }

    async load() {
      const [describe, status] = await Promise.all([this.client.describe(), this.client.status()]);
      this.describe = describe;
      this.status = status;
      this.viewModel = NS.viewModel.build(describe, status, this.composition, this.choice);
      this.values = Object.assign({}, this.viewModel.initialValues);
      this.render();
      return this.viewModel;
    }

    render() {
      const vm = this.viewModel;
      clear(this.container);
      this.container.appendChild(el('div', 'run-shell'));
      const shell = this.container.firstChild;

      /* A nonconformant document stops the page here. SPEC 7 says a client
       * meeting a credential value SHOULD refuse to proceed; this one does,
       * and says why rather than rendering a form that must not be used. */
      if (vm.refuseReason) {
        shell.appendChild(el('p', 'run-refuse', vm.refuseReason));
        return;
      }

      shell.appendChild(this.renderHeader(vm));
      shell.appendChild(this.renderReadiness(vm));
      const templates = this.renderTemplates(vm);
      if (templates) shell.appendChild(templates);
      const conversation = this.renderConversation(vm);
      if (conversation) shell.appendChild(conversation);
      shell.appendChild(this.renderForm(vm));
      shell.appendChild(this.renderControls(vm));
      shell.appendChild(this.renderOutput());
    }

    renderHeader(vm) {
      const section = el('section', 'run-header');
      section.appendChild(el('h1', 'run-title', vm.header.name));
      if (vm.header.summary) section.appendChild(el('p', 'run-summary', vm.header.summary));
      const meta = el('p', 'run-meta');
      /* Verbatim, both of them: the identifier is what the buyer types into
       * `.env`, and a client that prettified it would be describing a
       * different agent from the one the runner names at boot. */
      meta.appendChild(el('code', 'run-agent-id', vm.header.id));
      if (vm.header.version) meta.appendChild(el('span', 'run-version', ' ' + vm.header.version));
      section.appendChild(meta);
      if (vm.updateNotice) section.appendChild(el('p', 'run-update', vm.updateNotice));
      /* Named rather than half-applied. A template this client cannot draw is
       * the one composition member whose fallback changes the whole page
       * rather than one control, so a seller previewing a draft is told why
       * their layout is the plain one instead of left to guess. */
      if (vm.presentation.unrecognisedTemplate) {
        section.appendChild(el('p', 'run-update',
          'This listing asks for the "' + vm.presentation.unrecognisedTemplate +
          '" layout, which this client does not draw. Showing the standard one.'));
      }
      return section;
    }

    renderReadiness(vm) {
      const section = el('section', 'run-readiness');

      const banner = vm.entitlement;
      if (banner.message) {
        section.appendChild(el('p', 'run-entitlement run-entitlement--' + banner.tone, banner.message));
      }
      if (vm.level.note) section.appendChild(el('p', 'run-level', vm.level.note));
      section.appendChild(el('p', 'run-state', 'Runner state: ' + (vm.state || 'unknown')));

      if (vm.readiness.rows.length) {
        const list = el('ul', 'run-credentials');
        vm.readiness.rows.forEach(function (row) {
          const item = el('li', 'run-credential' + (row.satisfied ? ' is-satisfied' : ' is-missing'));
          item.appendChild(el('code', 'run-credential-env', row.env));
          item.appendChild(el('span', 'run-credential-tick', row.satisfied ? ' set' : ' not set'));
          if (row.purpose) item.appendChild(el('span', 'run-credential-purpose', ' — ' + row.purpose));
          if (row.signupUrl && !row.satisfied) {
            const link = el('a', 'run-credential-link', 'Get a key');
            link.href = row.signupUrl;
            link.rel = 'noopener noreferrer';
            link.target = '_blank';
            item.appendChild(link);
          }
          list.appendChild(item);
        });
        section.appendChild(list);
        if (!vm.readiness.satisfied) section.appendChild(el('p', 'run-credential-where', vm.readiness.where));
      }
      return section;
    }

    /* The examples the seller captured are the templates: no template format
     * is invented here, and there is no control that edits anything but an
     * input value. Absent examples render nothing at all rather than an empty
     * row — which is what both of this repository's real documents do today. */
    renderTemplates(vm) {
      if (!vm.examples.length) return null;
      const section = el('section', 'run-templates');
      section.appendChild(el('h2', 'run-templates-title', 'Start from an example'));
      const row = el('div', 'run-template-row');
      const self = this;
      vm.examples.forEach(function (example) {
        const chip = el('button', 'run-template-chip', example.label);
        chip.type = 'button';
        chip.addEventListener('click', function () {
          self.values = Object.assign({}, NS.viewModel.initialValues(self.describe, example.index));
          self.syncFormValues();
          /* Editing the inputs makes any held key stale: SPEC 4.2 answers a
           * reused key carrying different inputs with a 409, and there is no
           * reason to trigger it. */
          self.idempotencyKey = '';
        });
        row.appendChild(chip);
      });
      section.appendChild(row);
      const first = vm.examples[0];
      if (first && first.output) {
        const shown = el('details', 'run-template-output');
        shown.appendChild(el('summary', null, 'What a result looks like'));
        shown.appendChild(el('pre', 'run-template-output-body', first.output));
        section.appendChild(shown);
      }
      return section;
    }

    /* The field a conversation carries: the reserved brief, where the runner
     * declares it as text. A runner with none has nothing to reply into, so
     * its page stays the one-run page whatever the host asked for. */
    conversationField() {
      if (!this.conversation || !this.viewModel) return null;
      const key = NS.viewModel.PROMPT_KEY;
      return this.viewModel.fields.find(field => field.key === key && field.type === 'text') || null;
    }

    /* The characters a reply may carry: the brief's own `max_length`, which the
     * runner enforces, and the host's bound or the view model's under it. */
    conversationLimit(field) {
      const bounds = [this.conversation.maxChars || NS.viewModel.CONVERSATION_MAX_CHARS];
      if (field.maxLength !== null) bounds.push(field.maxLength);
      return Math.min.apply(Math, bounds);
    }

    /* A conversation, above the form: each brief sent and the answer it got,
     * oldest first. No verb takes a `run_id` (SPEC 4.5), so a run cannot be
     * pointed at the one before it: the agent sees what came before because
     * the next brief carries it, which `conversationBrief` composes, and the
     * note says so. */
    renderConversation(vm) {
      if (!this.conversationField()) return null;
      const self = this;
      const section = el('section', 'run-conversation');
      section.setAttribute('aria-label', 'Conversation');
      this.nodes.turns = el('ol', 'run-turns');
      section.appendChild(this.nodes.turns);
      const foot = el('div', 'run-conversation-foot');
      this.nodes.conversationNote = el('p', 'run-conversation-note');
      foot.appendChild(this.nodes.conversationNote);
      const reset = el('button', 'run-conversation-reset', 'New conversation');
      reset.type = 'button';
      reset.addEventListener('click', function () { self.resetConversation(); });
      foot.appendChild(reset);
      section.appendChild(foot);
      this.nodes.conversation = section;
      this.nodes.conversationReset = reset;
      this.turns.forEach(function (turn) { self.appendTurn(turn, vm); });
      this.syncConversation();
      return section;
    }

    appendTurn(turn, vm) {
      const item = el('li', 'run-turn');
      item.appendChild(el('p', 'run-turn-who', 'You'));
      item.appendChild(el('p', 'run-turn-brief', turn.brief));
      item.appendChild(el('p', 'run-turn-who', vm.header.name || 'The agent'));
      const answer = el('div', 'run-turn-answer');
      /* Drawn as the result panel draws it, so a turn reads the same before
       * and after it moves up: structure for `markdown`, text for the rest. */
      if (vm.output.mode === 'markdown') this.renderMarkdown(turn.answer, answer);
      else answer.appendChild(el('p', 'run-turn-text', turn.answer));
      if (vm.output.mode === 'file') answer.appendChild(this.textDownloadLink(turn.answer));
      item.appendChild(answer);
      this.nodes.turns.appendChild(item);
    }

    syncConversation() {
      if (!this.nodes.conversation) return;
      this.nodes.conversation.hidden = this.turns.length === 0;
      this.nodes.conversationReset.disabled = Boolean(this.abortController);
      const dropped = this.lastDropped;
      this.nodes.conversationNote.textContent = dropped
        ? 'A reply sends the conversation above with it, except the ' +
          (dropped === 1 ? 'first message, which no longer fits' : dropped + ' oldest messages, which no longer fit') +
          '. The page forgets it when you leave.'
        : 'A reply sends the conversation above with it, so the agent can build on it. The page forgets it when you leave.';
    }

    recordTurn(message, answer) {
      const field = this.conversationField();
      const turn = { brief: message, answer: answer };
      this.turns.push(turn);
      this.appendTurn(turn, this.viewModel);
      /* The answer lives in the conversation now, so the result panel is
       * emptied rather than left showing it twice. The usage line and the
       * timeline stay: they are about the run that has just ended. */
      this.nodes.result.textContent = '';
      this.clearRendered();
      this.values[field.key] = '';
      const control = this.nodes.inputs[field.key];
      if (control) control.value = '';
      this.lastDropped = NS.viewModel.conversationBrief(this.turns, '', this.conversationLimit(field)).dropped;
      this.syncConversation();
      if (control) control.focus();
    }

    /* Reached only from its button, which `syncConversation` disables while a
     * run is in flight, so a run never records into a conversation it did not
     * start from. */
    resetConversation() {
      this.turns = [];
      this.lastDropped = 0;
      if (this.nodes.turns) {
        this.revokeDownloads(this.nodes.turns);
        clear(this.nodes.turns);
      }
      this.syncConversation();
      const field = this.conversationField();
      if (field && this.nodes.inputs[field.key]) this.nodes.inputs[field.key].focus();
    }

    revokeDownloads(scope) {
      Array.prototype.slice.call(scope.querySelectorAll('.run-download')).forEach(function (node) {
        URL.revokeObjectURL(node.href);
        node.parentNode.removeChild(node);
      });
    }

    clearRendered() {
      this.revokeDownloads(this.nodes.rendered);
      clear(this.nodes.rendered);
    }

    renderForm(vm) {
      const section = el('section', 'run-form');
      const self = this;
      this.nodes.inputs = {};
      this.nodes.fields = {};
      this.nodes.choiceAbout = null;
      vm.fields.forEach(function (field) {
        const wrap = el('div', 'run-field');
        const id = self.scopedId('field-' + field.key);
        const label = el('label', 'run-field-label', field.label + (field.required ? ' *' : ''));
        label.htmlFor = id;
        wrap.appendChild(label);

        let control;
        if (field.type === 'select') {
          control = el('select', 'run-field-control');
          field.options.forEach(function (option) {
            const node = el('option', null, option);
            node.value = option;
            control.appendChild(node);
          });
        } else if (field.multiline) {
          control = el('textarea', 'run-field-control run-field-control--large');
          control.rows = 6;
        } else {
          control = el('input', 'run-field-control');
          control.type = field.type === 'number' ? 'number' : 'text';
          if (field.min !== null) control.min = String(field.min);
          if (field.max !== null) control.max = String(field.max);
        }
        control.id = id;
        control.name = field.key;
        if (field.required) control.required = true;
        if (field.maxLength !== null && control.tagName !== 'SELECT') {
          control.maxLength = field.maxLength;
        }
        /* Seller prose, and it reaches the page the only way anything does
         * here: as text. `el()` writes `textContent`, and a placeholder is an
         * attribute the browser never parses as markup. */
        if (field.placeholder && control.tagName !== 'SELECT') control.placeholder = field.placeholder;
        control.value = self.values[field.key] === undefined ? '' : String(self.values[field.key]);
        const chooses = Boolean(vm.choice) && field.key === vm.choice.key;
        control.addEventListener('input', function () {
          self.values[field.key] = control.value;
          self.idempotencyKey = '';
          if (chooses) self.syncChoice();
        });
        control.addEventListener('change', function () {
          self.values[field.key] = control.value;
          self.idempotencyKey = '';
          if (chooses) self.syncChoice();
        });
        wrap.appendChild(control);

        /* Said rather than hidden. The runner validates again on `run`, so
         * this form's checking is a courtesy — but a type it flattened to a
         * text box is a fact the buyer is entitled to see. */
        if (field.unrecognisedType) {
          wrap.appendChild(el('p', 'run-field-note',
            'This runner declared type "' + field.declaredType + '", which this client does not know. Showing a text box.'));
        }
        if (field.help) wrap.appendChild(el('p', 'run-field-help', field.help));
        /* What the chosen option does, beneath the control that chooses it,
         * rewritten on every change of it. */
        if (chooses) {
          self.nodes.choiceAbout = el('p', 'run-field-help');
          wrap.appendChild(self.nodes.choiceAbout);
        }
        /* Said, not swallowed. A widget a field's declared type cannot wear is
         * a composition the seller wrote and this page did not apply, and the
         * one place they will see that is the preview they are standing in. */
        if (field.ignoredWidget) {
          wrap.appendChild(el('p', 'run-field-note',
            'This listing asks for a "' + field.ignoredWidget + '" control, which a ' + field.type +
            ' input cannot be. Showing the ' + field.type + ' control the runner declared.'));
        }
        section.appendChild(wrap);
        self.nodes.inputs[field.key] = control;
        self.nodes.fields[field.key] = wrap;
      });
      this.syncChoice();
      return section;
    }

    /* Only the chosen option's fields are on show. The others are hidden
     * rather than removed, so what was typed into them is still there if the
     * buyer chooses that option again, and `runInputs` leaves them out of the
     * run either way. */
    syncChoice() {
      const vm = this.viewModel;
      if (!vm || !vm.choice) return;
      const chosen = this.values[vm.choice.key] === undefined ? '' : String(this.values[vm.choice.key]);
      const self = this;
      vm.fields.forEach(function (field) {
        const wrap = self.nodes.fields && self.nodes.fields[field.key];
        if (wrap && field.group) wrap.hidden = field.group !== chosen;
      });
      if (this.nodes.choiceAbout) {
        const about = Object.prototype.hasOwnProperty.call(vm.choice.about, chosen) ? vm.choice.about[chosen] : '';
        this.nodes.choiceAbout.textContent = about;
        this.nodes.choiceAbout.hidden = !about;
      }
    }

    renderControls(vm) {
      const section = el('section', 'run-controls');
      const self = this;

      if (vm.capabilities.tools.length) {
        const tools = el('details', 'run-tools');
        tools.appendChild(el('summary', null, 'What this agent can do'));
        const list = el('ul', 'run-tool-list');
        vm.capabilities.tools.forEach(function (tool) {
          list.appendChild(el('li', 'run-tool', tool));
        });
        tools.appendChild(list);
        section.appendChild(tools);
      }

      /* SPEC 4.1.2 makes surfacing these a SHOULD and the confirmation a MAY.
       * The confirmation is required here, once per session, and it carries
       * the sentence SPEC 4.5 forces: an abort is not a rollback, so money
       * spent before the tab closed stays spent. */
      if (vm.capabilities.needsWriteConfirmation) {
        const gate = el('div', 'run-write-gate');
        const box = el('input', 'run-write-check');
        box.type = 'checkbox';
        box.id = this.scopedId('write-confirm');
        const label = el('label', 'run-write-label',
          'This agent can ' + vm.capabilities.writeTools.join(', ') +
          '. Runs cost money and write outside this page; stopping a run or closing the tab cancels it but does not undo it.');
        label.htmlFor = box.id;
        box.addEventListener('change', function () {
          self.writeToolsAccepted = box.checked;
          self.syncRunEnabled();
        });
        gate.appendChild(box);
        gate.appendChild(label);
        section.appendChild(gate);
        this.nodes.writeGate = box;
      }

      const actions = el('div', 'run-actions');
      /* The seller's word for the action, where they gave one. It is prose
       * bounded and scanned at publish and written here with `textContent`,
       * so the widest thing it can do is be a long word. */
      const runButton = el('button', 'run-go', vm.presentation.runLabel || 'Run');
      runButton.type = 'button';
      runButton.addEventListener('click', function () { self.start({ reuseKey: false }); });
      actions.appendChild(runButton);

      const stopButton = el('button', 'run-stop', 'Stop');
      stopButton.type = 'button';
      stopButton.disabled = true;
      stopButton.addEventListener('click', function () { self.stop(); });
      actions.appendChild(stopButton);

      const retryButton = el('button', 'run-retry', 'Retry');
      retryButton.type = 'button';
      retryButton.disabled = true;
      retryButton.addEventListener('click', function () { self.start({ reuseKey: true }); });
      actions.appendChild(retryButton);
      section.appendChild(actions);

      /* Absent means the runner imposes no bound, and saying nothing is the
       * only honest rendering of that. */
      if (vm.limits.maxRunSeconds !== null) {
        section.appendChild(el('p', 'run-limit', 'This runner stops a run after ' + vm.limits.maxRunSeconds + 's.'));
      }
      if (!vm.capabilities.idempotentRetry) {
        section.appendChild(el('p', 'run-retry-note',
          'Retry runs the agent again — this runner does not offer free replays.'));
      }

      this.nodes.run = runButton;
      this.nodes.stop = stopButton;
      this.nodes.retry = retryButton;
      this.syncRunEnabled();
      return section;
    }

    renderOutput() {
      const section = el('section', 'run-output');
      this.nodes.notice = el('p', 'run-notice');
      this.nodes.timeline = el('ol', 'run-timeline');
      this.nodes.result = el('pre', 'run-result');
      /* A second node rather than a mode switch on the first. The `<pre>` is
       * what a stream fills token by token — partial markdown is not markdown,
       * so it stays plain until the run ends — and `<pre>` accepts phrasing
       * content only, so headings and lists cannot be built inside it. */
      this.nodes.rendered = el('div', 'run-result-rendered');
      this.nodes.usage = el('p', 'run-usage');
      this.nodes.output = section;
      section.appendChild(this.nodes.notice);
      section.appendChild(this.nodes.timeline);
      section.appendChild(this.nodes.result);
      section.appendChild(this.nodes.rendered);
      section.appendChild(this.nodes.usage);
      return section;
    }

    syncFormValues() {
      const self = this;
      Object.keys(this.nodes.inputs || {}).forEach(function (key) {
        const control = self.nodes.inputs[key];
        const value = self.values[key];
        control.value = value === undefined || value === null ? '' : String(value);
      });
      this.syncChoice();
    }

    syncRunEnabled() {
      if (!this.nodes.run) return;
      const vm = this.viewModel;
      const blockedByEntitlement = vm.entitlement.tone === 'blocked';
      const blockedByWriteTools = vm.capabilities.needsWriteConfirmation && !this.writeToolsAccepted;
      this.nodes.run.disabled = Boolean(
        !vm.level.canRun || blockedByEntitlement || blockedByWriteTools || this.abortController
      );
    }

    setNotice(message) {
      this.nodes.notice.textContent = message || '';
    }

    stop() {
      /* There is no cancel verb and there will not be one: aborting the
       * request *is* the cancellation (SPEC 4.5). */
      if (this.abortController) this.abortController.abort();
    }

    async start(options) {
      const settings = options || {};
      const vm = this.viewModel;
      if (!vm.level.canRun || this.abortController) return;

      const inputs = NS.viewModel.runInputs(vm.fields, this.values, vm.choice);
      /* In a conversation the brief carries the turns before it. What was
       * typed is kept apart, because that, not the composed whole, is the
       * turn the page records. */
      const brief = this.conversationField();
      const message = brief && this.values[brief.key] !== undefined ? String(this.values[brief.key]) : '';
      if (brief && this.turns.length && Object.prototype.hasOwnProperty.call(inputs, brief.key)) {
        const reply = NS.viewModel.conversationBrief(this.turns, message, this.conversationLimit(brief));
        inputs[brief.key] = reply.value;
        this.lastDropped = reply.dropped;
      }
      const inputsKey = JSON.stringify(inputs);
      if (vm.capabilities.idempotentRetry) {
        /* A replay is free only for the same inputs under the same key. Edited
         * inputs already cleared the key; this covers the first run too. */
        if (!settings.reuseKey || !this.idempotencyKey || this.lastInputsKey !== inputsKey) {
          this.idempotencyKey = randomKey();
        }
      } else {
        this.idempotencyKey = '';
      }
      this.lastInputsKey = inputsKey;

      clear(this.nodes.timeline);
      this.nodes.result.textContent = '';
      this.nodes.usage.textContent = '';
      /* A download link is appended beside the result rather than inside it,
       * so clearing the result does not remove it — two `bytes` runs would
       * otherwise leave two links, the older one pointing at the earlier
       * result and no way to tell them apart. Only the result panel's: a turn
       * of the conversation keeps the link to its own answer. */
      this.revokeDownloads(this.nodes.output);
      this.lastAnswer = null;
      /* And a field marked invalid by the last refusal stops being marked, or
       * a corrected input keeps wearing the error that has just been fixed. */
      Object.keys(this.nodes.inputs || {}).forEach(key => {
        this.nodes.inputs[key].classList.remove('is-invalid');
      });
      this.setNotice(vm.level.streams ? 'Running…' : 'Running — this runner returns the whole result at once.');
      this.abortController = new AbortController();
      this.nodes.stop.disabled = false;
      this.nodes.retry.disabled = true;
      this.syncRunEnabled();
      this.syncConversation();
      this.onRunStateChange('running');

      /* A turn is recorded only for an answer the page could show as text: a
       * refusal, a stop, a file or a type this client cannot draw leaves the
       * brief in its field, to send again or change. */
      let recorded = false;
      try {
        if (vm.level.streams) await this.runStreamed(inputs);
        else await this.runWhole(inputs);
        if (brief && this.lastAnswer !== null) {
          this.recordTurn(message, this.lastAnswer);
          recorded = true;
        }
      } catch (err) {
        this.reportFailure(err);
      } finally {
        this.abortController = null;
        this.nodes.stop.disabled = true;
        /* Nothing to retry once the answer is a turn: its brief has left the
         * field, and a reply is the next thing to send. */
        this.nodes.retry.disabled = recorded;
        this.syncRunEnabled();
        this.syncConversation();
        this.onRunStateChange('idle');
      }
    }

    async runWhole(inputs) {
      const body = await this.client.run(inputs, {
        signal: this.abortController.signal,
        idempotencyKey: this.idempotencyKey,
      });
      this.lastRunId = body && body.run_id ? String(body.run_id) : '';
      /* Cleared *before* the result speaks, never after. `showResult` sets its
       * own notice for an output type this client cannot show, and a trailing
       * `setNotice('')` here would wipe exactly that message — leaving a blank
       * page for the one case SPEC 4.1.4 says a client may not pass over. */
      this.setNotice('');
      this.showResult(body && body.output);
      this.showUsage(body && body.usage);
    }

    async runStreamed(inputs) {
      const self = this;
      let streamed = '';
      let sawDelta = false;
      let done = null;

      await this.client.stream(inputs, {
        signal: this.abortController.signal,
        idempotencyKey: this.idempotencyKey,
        onEvent: function (event) {
          if (event.name === 'start') {
            self.lastRunId = event.payload && event.payload.run_id ? String(event.payload.run_id) : '';
            self.addTimelineEntry('Run started');
          } else if (event.name === 'step') {
            self.addStep(event.payload);
          } else if (event.name === 'delta') {
            const chunk = event.payload && event.payload.text ? String(event.payload.text) : '';
            streamed += chunk;
            sawDelta = true;
            /* textContent, appended: a delta is agent output and never markup. */
            self.nodes.result.textContent = streamed;
          } else if (event.name === 'done') {
            done = event.payload;
          } else if (event.name === 'error') {
            done = null;
            self.showStreamError(event.payload);
          }
          /* Any other event name is skipped (MUST) — a later minor release may
           * add one, and a client that threw would break on it. */
        },
      });

      if (done) {
        const output = done.output;
        const finalText = output && output.value !== undefined ? String(output.value) : '';
        this.setNotice('');
        this.showResult(output);
        /* SPEC 4.3's SHOULD, applied in full because this client *can* replace
         * what it rendered: where the concatenated deltas and the final value
         * disagree, the final value wins and the buyer is told the streamed
         * text was superseded rather than left wondering which they read.
         *
         * This cannot collide with the notice `showResult` may have just set:
         * that one fires only for an output type which is not `text`, and this
         * one only for `text`. */
        if (sawDelta && output && output.type === 'text' && streamed !== finalText) {
          this.setNotice('The streamed text was superseded by the final result.');
        }
        this.showUsage(done.usage);
      }
    }

    addTimelineEntry(label) {
      this.nodes.timeline.appendChild(el('li', 'run-timeline-entry', label));
    }

    addStep(payload) {
      if (!payload) return;
      const phase = payload.phase ? String(payload.phase) : '';
      const name = payload.name ? String(payload.name) : 'step';
      const parts = [name, phase].filter(Boolean);
      /* `latency_ms` arrives on `finished` only, and `model_id` when a model
       * was called — so both are appended where present rather than assumed. */
      if (typeof payload.latency_ms === 'number') parts.push(payload.latency_ms + 'ms');
      if (payload.model_id) parts.push(String(payload.model_id));
      this.addTimelineEntry(parts.join(' · '));
    }

    showResult(output) {
      const plan = this.viewModel.output;
      const node = this.nodes.result;
      clear(this.nodes.rendered);
      if (!output) {
        node.textContent = '';
        return;
      }
      const type = output.type ? String(output.type) : plan.type;

      if (type === 'bytes') {
        /* Never shown as text (MUST). Offered as a file named by its media
         * type, which is the only thing the protocol says about it. */
        node.textContent = '';
        this.offerDownload(output);
        return;
      }
      if (type !== 'text') {
        /* The one unknown a client may not ignore: named, and the run is not
         * reported as failed. */
        node.textContent = '';
        this.setNotice('This agent returned "' + type + '", which this client cannot show. The run itself succeeded.');
        return;
      }

      const value = output.value === undefined || output.value === null ? '' : String(output.value);
      this.lastAnswer = value;
      if (plan.mode === 'markdown') {
        /* Structure only, and built node by node — see `outputBlocks`. The
         * `<pre>` is emptied rather than left carrying the stream's plain copy
         * underneath the rendered one. */
        node.textContent = '';
        this.renderMarkdown(value);
        return;
      }
      node.textContent = value;
      if (plan.mode === 'file') {
        /* Offered *beside* the text, never instead of it. A result the buyer
         * cannot read until they have saved it is a worse page than one they
         * can read and also save. */
        this.offerTextDownload(value);
      }
    }

    renderMarkdown(value, into) {
      const target = into || this.nodes.rendered;
      NS.viewModel.outputBlocks(value).forEach(function (block) {
        if (block.kind === 'heading') {
          /* Clamped to h2..h4: the page's own `<h1>` is the agent's name, and
           * an agent that emits `#` is describing its answer, not the page. */
          const level = Math.min(4, Math.max(2, block.level + 1));
          target.appendChild(el('h' + level, 'run-md-heading', block.text));
          return;
        }
        if (block.kind === 'code') {
          target.appendChild(el('pre', 'run-md-code', block.text));
          return;
        }
        if (block.kind === 'list') {
          const list = el(block.ordered ? 'ol' : 'ul', 'run-md-list');
          block.items.forEach(function (item) { list.appendChild(el('li', 'run-md-item', item)); });
          target.appendChild(list);
          return;
        }
        target.appendChild(el('p', 'run-md-paragraph', block.text));
      });
    }

    offerTextDownload(value) {
      this.nodes.rendered.appendChild(this.textDownloadLink(value));
    }

    textDownloadLink(value) {
      const link = el('a', 'run-download', 'Download the result');
      link.href = URL.createObjectURL(new Blob([value], { type: 'text/plain' }));
      link.download = 'result.txt';
      return link;
    }

    offerDownload(output) {
      const mediaType = output.media_type ? String(output.media_type) : 'application/octet-stream';
      const link = el('a', 'run-download', 'Download the result (' + mediaType + ')');
      let blob;
      try {
        const binary = root.atob(String(output.value || ''));
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        blob = new Blob([bytes], { type: mediaType });
      } catch (err) {
        this.setNotice('This agent returned a file this client could not decode.');
        return;
      }
      link.href = URL.createObjectURL(blob);
      link.download = 'result';
      this.nodes.result.parentNode.insertBefore(link, this.nodes.usage);
    }

    showUsage(usage) {
      if (!usage) {
        this.nodes.usage.textContent = '';
        return;
      }
      const parts = [];
      if (typeof usage.input_tokens === 'number') parts.push(usage.input_tokens + ' in');
      if (typeof usage.output_tokens === 'number') parts.push(usage.output_tokens + ' out');
      if (typeof usage.cost_usd === 'number') parts.push('$' + usage.cost_usd);
      if (!parts.length) {
        this.nodes.usage.textContent = '';
        return;
      }
      /* SPEC 4.2: `cost_usd` is an estimate a client MUST NOT treat as billed,
       * so the label says so rather than leaving a number to be read as one. */
      this.nodes.usage.textContent = 'Usage (advisory, not a bill): ' + parts.join(' · ');
    }

    showStreamError(payload) {
      const error = payload && payload.error ? payload.error : {};
      const refusal = new NS.PosternRefusal(0, error.code, error.message, error.detail);
      this.reportFailure(refusal);
    }

    reportFailure(err) {
      if (err && err.name === 'AbortError') {
        this.setNotice('Stopped. The runner cancels a run when the client disconnects.');
        return;
      }
      if (err instanceof NS.PosternUnreachable) {
        this.setNotice(err.message);
        return;
      }
      const vm = this.viewModel;
      const guidance = NS.refusalGuidance(err, {
        credentials: vm.readiness.rows,
        level: vm.level.level,
        state: vm.state,
        runId: this.lastRunId,
        maxConcurrentRuns: vm.limits.maxConcurrentRuns,
        runInFlight: true,
        hosted: this.hosted,
      });
      /* The runner's own sentence first, verbatim, then the next move. That is
       * right whenever the person reading operates the runner: they own the
       * machine and they set the keys, so the provider's own words are the most
       * useful thing this page can say — it names precisely what to go and fix.
       *
       * A hosted run inverts who the operator is, and `agent_error` is the one
       * refusal whose message carries whatever the provider said about
       * *the operator's* credentials: a 401 quoting the last characters of the
       * operator's own key, and a link to a dashboard the buyer has no account on.
       * Suppressed for that code alone — every other arm describes the request
       * or the runner's own configuration, which a buyer can act on in either
       * deployment.
       *
       * Suppressed here, not dropped: the run trace keeps the sentence, and an
       * admin reads it on the run page's own history. The seller-scoped runs
       * endpoint withholds it too, because on a hosted run the seller does not
       * operate the key either. */
      const relayed = !(this.hosted && err && err.code === 'agent_error');
      const message = relayed && err && err.message ? String(err.message) : '';
      this.setNotice([message, guidance.nextMove].filter(Boolean).join(' '));
      if (guidance.highlightKey && this.nodes.inputs[guidance.highlightKey]) {
        this.nodes.inputs[guidance.highlightKey].classList.add('is-invalid');
        this.nodes.inputs[guidance.highlightKey].focus();
      }
    }
  }

  root.Gatehouse = Object.assign({}, root.Gatehouse, { RunRenderer });

  /* `SigrixRun` is the name this package was born under, inside one host. It
   * is kept pointing at the same object so that host keeps working across the
   * extraction, and because a global is the one part of a browser package a
   * consumer cannot alias for itself. Deprecated; removed at 1.0. */
  root.SigrixRun = root.Gatehouse;
})(typeof window !== 'undefined' ? window : globalThis);
