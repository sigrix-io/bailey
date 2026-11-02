// Bailey's page. It talks to the app server on this computer and nowhere else.
//
// The session's token arrives in the launch address's fragment (#token=...), which a
// browser never sends to a server. The page keeps it in this origin's storage, takes it
// out of the address bar, and sends it on every request. Everything it shows is written
// as text (textContent), never as markup: what the server or a model returns is data.
(function () {
  "use strict";

  var STORAGE_KEY = "bailey-session";
  var token = takeToken();
  var settings = null;
  var chat = { assistants: null, conversation: null, streaming: false, pending: [], lastReply: null };
  var editing = null;
  var toolsOf = null;
  var knowledgeOf = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function element(tag, className, text) {
    var made = document.createElement(tag);
    if (className) {
      made.className = className;
    }
    if (text !== undefined) {
      made.textContent = text;
    }
    return made;
  }

  function readStored() {
    try {
      return window.localStorage.getItem(STORAGE_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function store(value) {
    try {
      if (value) {
        window.localStorage.setItem(STORAGE_KEY, value);
      } else {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch (error) {
      // Storage refused (a private window, a browser setting): the token lives in this
      // tab's memory alone, which is enough until the tab closes.
    }
  }

  function takeToken() {
    var match = /(?:^#|&)token=([^&]+)/.exec(window.location.hash);
    if (match) {
      var fresh = decodeURIComponent(match[1]);
      store(fresh);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      return fresh;
    }
    return readStored();
  }

  function showOnly(panel) {
    byId("loading").hidden = true;
    byId("no-session").hidden = panel !== byId("no-session");
    var panels = document.querySelectorAll("[data-panel]");
    for (var index = 0; index < panels.length; index += 1) {
      panels[index].hidden = panels[index] !== panel;
    }
  }

  function lostSession() {
    token = "";
    store("");
    byId("nav").hidden = true;
    showOnly(byId("no-session"));
  }

  function request(method, path, body, contentType) {
    var options = { method: method, headers: { Authorization: "Bearer " + token } };
    if (body !== undefined) {
      options.headers["Content-Type"] = contentType || "application/json";
      options.body = contentType ? body : JSON.stringify(body);
    }
    return fetch(path, options).then(function (response) {
      if (response.status === 401) {
        lostSession();
        throw new Error("This session has ended.");
      }
      return response;
    });
  }

  function refusal(response) {
    return response.json().then(
      function (answer) {
        throw new Error(answer && answer.error ? answer.error : "Bailey could not do that.");
      },
      function () {
        throw new Error("Bailey answered with something this page cannot read.");
      }
    );
  }

  function api(method, path, body, contentType) {
    return request(method, path, body, contentType).then(function (response) {
      if (!response.ok) {
        return refusal(response);
      }
      return response.json();
    });
  }

  function say(id, text, isError) {
    var line = byId(id);
    line.textContent = text;
    line.classList.toggle("error", Boolean(isError));
  }

  // Views -------------------------------------------------------------------

  function openView(name) {
    var buttons = document.querySelectorAll("[data-view]");
    for (var index = 0; index < buttons.length; index += 1) {
      if (buttons[index].getAttribute("data-view") === name) {
        buttons[index].setAttribute("aria-current", "page");
      } else {
        buttons[index].removeAttribute("aria-current");
      }
    }
    showOnly(document.querySelector('[data-panel="' + name + '"]'));
    if (name === "assistants" || name === "chat") {
      loadAssistants().catch(function (error) {
        say(name === "chat" ? "chat-status" : "assistants-status", error.message, true);
      });
    }
    if (name === "settings") {
      loadGrants().catch(function (error) {
        say("grants-status", error.message, true);
      });
    }
  }

  // Chat --------------------------------------------------------------------

  function activeAssistant() {
    if (!chat.assistants) {
      return null;
    }
    for (var index = 0; index < chat.assistants.assistants.length; index += 1) {
      if (chat.assistants.assistants[index].id === chat.assistants.active) {
        return chat.assistants.assistants[index];
      }
    }
    return null;
  }

  // The list of assistants is replaced by each answer that carries it. A load asked for
  // before a change (an import, say) can answer after it, so a load is dropped when a
  // newer list has been taken since it was asked for.
  var assistantsSeen = 0;

  function adoptAssistants(view) {
    assistantsSeen += 1;
    chat.assistants = view;
  }

  function loadAssistants() {
    var asked = assistantsSeen;
    return api("GET", "/api/assistants").then(function (view) {
      if (asked !== assistantsSeen) {
        return null;
      }
      adoptAssistants(view);
      drawAssistantPick();
      drawAssistantList();
      drawTemplates();
      return loadConversations();
    });
  }

  function drawAssistantPick() {
    var pick = byId("assistant-pick");
    pick.replaceChildren();
    chat.assistants.assistants.forEach(function (assistant) {
      var option = element("option", "", assistant.title);
      option.value = assistant.id;
      option.selected = assistant.id === chat.assistants.active;
      pick.append(option);
    });
  }

  function loadConversations() {
    return api("GET", "/api/conversations").then(function (answer) {
      drawConversationList(answer.conversations);
      var known = answer.conversations.some(function (entry) {
        return entry.id === chat.conversation;
      });
      if (known) {
        return null;
      }
      chat.conversation = null;
      if (answer.conversations.length) {
        return openConversation(answer.conversations[0].id);
      }
      drawMessages([]);
      return null;
    });
  }

  function drawConversationList(conversations) {
    var list = byId("conversation-list");
    list.replaceChildren();
    conversations.forEach(function (entry) {
      var item = element("li");
      var button = element("button", "conversation", entry.title);
      button.type = "button";
      if (entry.id === chat.conversation) {
        button.setAttribute("aria-current", "true");
      }
      button.addEventListener("click", function () {
        openConversation(entry.id);
      });
      item.append(button);
      list.append(item);
    });
  }

  function openConversation(id) {
    return api("GET", "/api/conversations/" + encodeURIComponent(id)).then(function (conversation) {
      chat.conversation = conversation.id;
      drawMessages(conversation.messages);
      drawQuestion(conversation.pending || []);
      var buttons = byId("conversation-list").querySelectorAll("button");
      for (var index = 0; index < buttons.length; index += 1) {
        buttons[index].removeAttribute("aria-current");
      }
      return api("GET", "/api/conversations").then(function (answer) {
        drawConversationList(answer.conversations);
      });
    });
  }

  function money(usd) {
    var amount = Number(usd);
    if (!isFinite(amount)) {
      return "";
    }
    return "$" + (amount < 0.1 ? amount.toFixed(4) : amount.toFixed(2));
  }

  function metaLine(entry) {
    var parts = [];
    if (entry.skills && entry.skills.length) {
      parts.push((entry.skills.length === 1 ? "Skill: " : "Skills: ") + entry.skills.join(", "));
    }
    if (entry.usage) {
      parts.push(
        entry.usage.input_tokens.toLocaleString() + " tokens in, " + entry.usage.output_tokens.toLocaleString() + " out"
      );
      if (entry.estimate && entry.estimate.usd !== undefined) {
        parts.push("about " + money(entry.estimate.usd) + " (an estimate)");
      } else {
        parts.push("no price estimate for this model");
      }
    }
    return parts.join(" · ");
  }

  function appendEntry(entry) {
    var bubble = element("div", "message message-" + entry.role);
    var tools = element("ul", "message-tools");
    var text = element("p", "message-text", entry.text || "");
    var meta = element("p", "message-meta", entry.role === "assistant" ? metaLine(entry) : "");
    bubble.append(tools, text, meta);
    byId("messages").append(bubble);
    bubble.scrollIntoView({ block: "end" });
    var made = {
      bubble: bubble,
      text: text,
      meta: meta,
      tools: tools,
      lines: {},
      skills: (entry.skills || []).slice(),
      usage: entry.usage || null,
      estimate: entry.estimate || null,
    };
    (entry.searches || []).forEach(function (query) {
      showSearch(made, query);
    });
    (entry.tools || []).forEach(function (call) {
      showTool(made, call);
    });
    showCitations(made, entry.citations || []);
    return made;
  }

  function showSearch(reply, query) {
    reply.tools.append(element("li", "tool-line tool-success", "Searched the documents for \u201c" + query + "\u201d."));
  }

  // Each passage an answer cites, as its file, heading and text, under the answer.
  function showCitations(reply, citations) {
    var old = reply.bubble.querySelector(".citations");
    if (old) {
      old.remove();
    }
    if (!citations.length) {
      return;
    }
    var box = element("div", "citations");
    box.append(element("p", "citations-title", citations.length === 1 ? "Source" : "Sources"));
    citations.forEach(function (citation) {
      var item = element("details", "citation");
      var where = citation.document + " \u203a " + citation.heading + (citation.page ? ", page " + citation.page : "");
      item.append(element("summary", "", "[P" + citation.id + "] " + where));
      item.append(element("p", "citation-text", citation.text));
      box.append(item);
    });
    reply.bubble.insertBefore(box, reply.meta);
  }

  // One line per call of a server's tool, saying how it went, as text.
  function toolWords(call) {
    var named = call.tool + (call.server ? " (" + call.server + ")" : "");
    if (call.outcome === "success") {
      return call.asked ? "You allowed " + named + ", and it ran." : "Used " + named + ".";
    }
    if (call.outcome === "denied") {
      return "You declined " + named + ". It did not run.";
    }
    if (call.outcome === "waiting") {
      return named + " is waiting for your answer.";
    }
    if (call.outcome === "failed") {
      return named + " failed.";
    }
    if (call.outcome === "retry") {
      return named + " was given another try.";
    }
    if (call.outcome === "interrupted") {
      return named + " was interrupted.";
    }
    return "Using " + named + "…";
  }

  function showTool(reply, call) {
    var known = reply.lines[call.id];
    if (known) {
      if (call.outcome !== undefined) {
        known.call.outcome = call.outcome;
      }
      if (call.asked) {
        known.call.asked = true;
      }
    } else {
      known = { call: call, line: element("li", "tool-line") };
      reply.lines[call.id] = known;
      reply.tools.append(known.line);
    }
    known.line.textContent = toolWords(known.call);
    known.line.className = "tool-line tool-" + (known.call.outcome || "running");
  }

  function drawMessages(entries) {
    byId("messages").replaceChildren();
    chat.lastReply = null;
    entries.forEach(function (entry) {
      var drawn = appendEntry(entry);
      // A run that stopped to ask goes on in its own reply, after a reload too.
      chat.lastReply = entry.role === "assistant" ? drawn : null;
    });
    drawStarters(entries.length === 0);
  }

  // A run that stopped to ask and the run its answer resumed share one reply, and their
  // usage is added, as the server adds it for a reloaded conversation.
  function addedUsage(before, more) {
    if (!before) {
      return more;
    }
    var sum = {};
    Object.keys(before)
      .concat(Object.keys(more))
      .forEach(function (key) {
        sum[key] = (before[key] || 0) + (more[key] || 0);
      });
    return sum;
  }

  function addedEstimate(before, more) {
    if (!before || !more) {
      return more || before;
    }
    return { usd: String(Number(before.usd) + Number(more.usd)) };
  }

  function drawStarters(show) {
    var box = byId("starters");
    box.replaceChildren();
    var assistant = activeAssistant();
    if (!show || !assistant) {
      return;
    }
    if (!assistant.starters.length) {
      box.append(element("p", "hint", "Ask " + assistant.title + " anything."));
      return;
    }
    box.append(element("p", "hint", "Start with one of these, or write your own."));
    assistant.starters.forEach(function (starter) {
      var button = element("button", "quiet starter", starter);
      button.type = "button";
      button.addEventListener("click", function () {
        var field = byId("message");
        // A starter that ends asking for more ("Summarise this:") gets the space the
        // person would type before their own words.
        field.value = /[:\u2026]$/.test(starter) ? starter + " " : starter;
        field.focus();
        field.setSelectionRange(field.value.length, field.value.length);
      });
      box.append(button);
    });
  }

  function startConversation() {
    return api("POST", "/api/conversations", {}).then(function (conversation) {
      chat.conversation = conversation.id;
      drawMessages([]);
      drawQuestion([]);
      return api("GET", "/api/conversations").then(function (answer) {
        drawConversationList(answer.conversations);
        return conversation.id;
      });
    });
  }

  function readEvents(response, onEvent) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) {
          return null;
        }
        buffer += decoder.decode(chunk.value, { stream: true });
        var pieces = buffer.split("\n\n");
        buffer = pieces.pop();
        pieces.forEach(function (piece) {
          if (piece.indexOf("data: ") === 0) {
            onEvent(JSON.parse(piece.slice(6)));
          }
        });
        return pump();
      });
    }
    return pump();
  }

  function send(text) {
    if (chat.streaming || !text.trim()) {
      return;
    }
    chat.streaming = true;
    byId("send").disabled = true;
    say("chat-status", "", false);
    var field = byId("message");
    var ready = chat.conversation ? Promise.resolve(chat.conversation) : startConversation();
    var mine = null;
    var reply = null;
    ready
      .then(function (conversationId) {
        drawStarters(false);
        field.value = "";
        mine = appendEntry({ role: "user", text: text });
        reply = appendEntry({ role: "assistant", text: "" });
        reply.meta.textContent = "Thinking…";
        return request("POST", "/api/conversations/" + encodeURIComponent(conversationId) + "/messages", { text: text });
      })
      .then(function (response) {
        if (!response.ok) {
          return refusal(response);
        }
        // The server named the conversation when it took the message: show the name now,
        // not once the whole answer has streamed in.
        api("GET", "/api/conversations")
          .then(function (answer) {
            drawConversationList(answer.conversations);
          })
          .catch(function () {
            return null;
          });
        return streamInto(reply, response);
      })
      .catch(function (error) {
        // Refused before anything was sent: the message comes back to the box.
        if (mine) {
          mine.bubble.remove();
        }
        if (reply) {
          reply.bubble.remove();
        }
        field.value = text;
        say("chat-status", error.message, true);
      })
      .then(function () {
        chat.streaming = false;
        byId("send").disabled = chat.pending.length > 0;
        return api("GET", "/api/conversations").then(function (answer) {
          drawConversationList(answer.conversations);
        });
      })
      .catch(function () {
        return null;
      });
  }

  // The events of one run, drawn into its reply as they arrive.
  function streamInto(reply, response) {
    chat.lastReply = reply;
    return readEvents(response, function (event) {
      if (event.type === "text") {
        reply.text.textContent += event.text;
        reply.meta.textContent = reply.skills.length ? "Skill: " + reply.skills.join(", ") : "";
      } else if (event.type === "skill") {
        if (reply.skills.indexOf(event.name) === -1) {
          reply.skills.push(event.name);
        }
        reply.meta.textContent = "Loading the " + event.name + " skill…";
      } else if (event.type === "status") {
        reply.meta.textContent = event.text;
      } else if (event.type === "search") {
        showSearch(reply, event.query);
        reply.meta.textContent = "Searching the documents\u2026";
      } else if (event.type === "tool") {
        showTool(reply, { id: event.id, tool: event.tool, server: event.server, outcome: "" });
        reply.meta.textContent = "Using " + event.tool + "…";
      } else if (event.type === "tool_result") {
        showTool(reply, { id: event.id, outcome: event.outcome });
      } else if (event.type === "approval") {
        event.calls.forEach(function (call) {
          showTool(reply, { id: call.id, tool: call.tool, server: call.server, outcome: "waiting", asked: true });
        });
        chat.pending = event.calls;
      } else if (event.type === "done") {
        reply.usage = addedUsage(reply.usage, event.usage);
        reply.estimate = addedEstimate(reply.estimate, event.estimate);
        reply.meta.textContent = metaLine({ skills: reply.skills, usage: reply.usage, estimate: reply.estimate });
        showCitations(reply, event.citations || []);
        if (event.paused) {
          drawQuestion(chat.pending);
        } else {
          say("chat-status", "Answer finished.", false);
        }
      } else if (event.type === "error") {
        var notice = appendEntry({ role: "notice", text: event.message });
        notice.bubble.scrollIntoView({ block: "end" });
        if (!reply.text.textContent && !reply.tools.childElementCount) {
          reply.bubble.remove();
        }
        reply.meta.textContent = "";
      }
    });
  }

  // A run that reached a tool which asks first stops there. The question waits in the
  // conversation, and the message box waits with it, until the person answers each call.
  function drawQuestion(calls) {
    chat.pending = calls || [];
    var open = chat.pending.length > 0;
    var list = byId("question-calls");
    list.replaceChildren();
    byId("question").hidden = !open;
    byId("message").disabled = open;
    byId("send").disabled = open || chat.streaming;
    if (!open) {
      return;
    }
    var assistant = activeAssistant();
    var who = assistant ? assistant.title : "The assistant";
    var answers = {};
    byId("question-title").textContent = chat.pending.length === 1 ? "Allow this tool to run?" : "Allow these tools to run?";
    chat.pending.forEach(function (call) {
      var item = element("div", "question-call");
      item.append(element("p", "question-text", who + " wants to run " + call.tool + (call.server ? " on " + call.server : "") + ", with:"));
      item.append(element("pre", "question-args", JSON.stringify(call.args || {}, null, 2)));
      var actions = element("div", "actions");
      var choices = [
        ["Allow once", "once", ""],
        ["Always for this assistant", "always", "quiet"],
        ["No", "no", "quiet"],
      ];
      choices.forEach(function (choice) {
        actions.append(
          button(choice[0], choice[2], function () {
            answers[call.id] = choice[1];
            var buttons = actions.querySelectorAll("button");
            for (var index = 0; index < buttons.length; index += 1) {
              buttons[index].disabled = true;
            }
            item.append(
              element(
                "p",
                "hint",
                choice[1] === "no" ? "Declined." : choice[1] === "always" ? "Allowed, now and from now on." : "Allowed this once."
              )
            );
            if (Object.keys(answers).length === chat.pending.length) {
              answerQuestions(answers);
            }
          })
        );
      });
      item.append(actions);
      list.append(item);
    });
    say("chat-status", "Answer the question above the message box to go on.", false);
  }

  function answerQuestions(answers) {
    if (chat.streaming || !chat.conversation) {
      return;
    }
    chat.streaming = true;
    var last = byId("messages").lastElementChild;
    var reply = chat.lastReply && chat.lastReply.bubble === last ? chat.lastReply : appendEntry({ role: "assistant", text: "" });
    reply.meta.textContent = "Going on…";
    request("POST", "/api/conversations/" + encodeURIComponent(chat.conversation) + "/answers", { answers: answers })
      .then(function (response) {
        if (!response.ok) {
          return refusal(response);
        }
        drawQuestion([]);
        return streamInto(reply, response);
      })
      .catch(function (error) {
        say("chat-status", error.message, true);
        return openConversation(chat.conversation);
      })
      .then(function () {
        chat.streaming = false;
        byId("send").disabled = chat.pending.length > 0;
      })
      .catch(function () {
        return null;
      });
  }

  byId("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    send(byId("message").value);
  });

  byId("message").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send(byId("message").value);
    }
  });

  byId("new-conversation").addEventListener("click", function () {
    startConversation().catch(function (error) {
      say("chat-status", error.message, true);
    });
  });

  byId("assistant-pick").addEventListener("change", function (event) {
    useAssistant(event.target.value);
  });

  byId("open-settings").addEventListener("click", function () {
    openView("settings");
  });

  function drawSetupNeeded() {
    var needed = "";
    if (!settings) {
      needed = "";
    } else if (settings.keys.locked) {
      needed = "Unlock your keys in Settings before you chat.";
    } else if (!settings.provider) {
      needed = "Choose your AI provider and save its key in Settings before you chat.";
    } else {
      var chosen = providerById(settings.provider);
      if (chosen && chosen.key_required && !(chosen.key && chosen.key.saved)) {
        needed = "Save your " + chosen.name + " key in Settings before you chat.";
      } else if (chosen && !settings.models[chosen.id]) {
        needed = "Give the model's name in Settings before you chat.";
      }
    }
    byId("setup-needed").hidden = !needed;
    byId("setup-needed-text").textContent = needed;
  }

  // Assistants ----------------------------------------------------------------

  function useAssistant(id) {
    return api("POST", "/api/assistants/" + encodeURIComponent(id) + "/use")
      .then(function (view) {
        adoptAssistants(view);
        chat.conversation = null;
        drawAssistantPick();
        drawAssistantList();
        return loadConversations();
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function drawTemplates() {
    var pick = byId("template-pick");
    var chosen = pick.value;
    pick.replaceChildren();
    chat.assistants.templates.forEach(function (made) {
      var option = element("option", "", made.title);
      option.value = made.id;
      option.selected = made.id === chosen;
      pick.append(option);
    });
  }

  function drawAssistantList() {
    var list = byId("assistant-list");
    list.replaceChildren();
    chat.assistants.assistants.forEach(function (assistant) {
      var item = element("li", "assistant-item");
      var words = element("div", "assistant-words");
      var title = element("p", "assistant-title", assistant.title);
      if (assistant.id === chat.assistants.active) {
        title.append(" ", element("span", "badge", "In use"));
      }
      if (assistant.unverified) {
        var mark = element("span", "badge unverified", "Unverified");
        mark.title = "Installed from a file, not from the Sigrix store: nobody has checked what it does.";
        title.append(" ", mark);
      }
      words.append(title);
      if (assistant.description) {
        words.append(element("p", "hint", assistant.description));
      }
      words.append(element("p", "hint", assistant.skills.length ? "Skills: " + assistant.skills.join(", ") : "No skills"));
      if (assistant.servers && assistant.servers.length) {
        words.append(element("p", "hint", "Tool servers: " + assistant.servers.join(", ")));
      }
      if (assistant.apps && assistant.apps.length) {
        words.append(element("p", "hint", "Apps: " + assistant.apps.join(", ")));
      }
      var actions = element("div", "actions");
      if (assistant.id !== chat.assistants.active) {
        actions.append(button("Use", "", function () {
          useAssistant(assistant.id);
        }));
      }
      actions.append(button("Edit", "quiet", function () {
        editAssistant(assistant.id);
      }));
      actions.append(button("Documents", "quiet", function () {
        openKnowledge(assistant.id);
      }));
      if (assistant.servers && assistant.servers.length) {
        actions.append(button("Tools", "quiet", function () {
          openTools(assistant.id);
        }));
      }
      if (assistant.apps && assistant.apps.length) {
        actions.append(button("Apps", "quiet", function () {
          openApps(assistant.id);
        }));
      }
      actions.append(button("Export", "quiet", function () {
        exportAssistant(assistant.id);
      }));
      actions.append(button("Delete", "quiet", function () {
        deleteAssistant(assistant);
      }));
      item.append(words, actions);
      list.append(item);
    });
  }

  function button(label, className, onClick) {
    var made = element("button", className, label);
    made.type = "button";
    made.addEventListener("click", onClick);
    return made;
  }

  byId("create-assistant").addEventListener("click", function () {
    api("POST", "/api/assistants", { template: byId("template-pick").value })
      .then(function (view) {
        adoptAssistants(view);
        drawAssistantPick();
        drawAssistantList();
        say("assistants-status", "Made. Edit it below, then use it.", false);
        return editAssistant(view.id);
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  });

  // Installing --------------------------------------------------------------
  // A package is checked and shown before anything is written (bailey.install): the
  // server answers with the install screen, holds what it checked, and writes exactly
  // that when the person presses Install.

  var installing = null;
  var packageLimits = null;

  function checkPackage(sent, name) {
    say("assistants-status", "Checking " + name + "…", false);
    sent
      .then(function (screen) {
        say("assistants-status", "", false);
        drawInstall(screen);
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function base64(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = "";
    for (var at = 0; at < bytes.length; at += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(at, at + 0x8000));
    }
    return btoa(binary);
  }

  byId("install-zip").addEventListener("change", function (event) {
    var file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    checkPackage(file.arrayBuffer().then(function (data) {
      return api("POST", "/api/installs", data, "application/zip");
    }), file.name);
  });

  byId("install-folder").addEventListener("change", function (event) {
    var picked = Array.prototype.slice.call(event.target.files || []);
    event.target.value = "";
    if (!picked.length) {
      return;
    }
    var name = (picked[0].webkitRelativePath || picked[0].name).split("/")[0];
    // A folder's version control and a Mac's folder settings are no part of a solution.
    var files = picked.filter(function (file) {
      var path = file.webkitRelativePath || file.name;
      return !/(^|\/)\.git\//.test(path) && !/(^|\/)\.DS_Store$/.test(path);
    });
    var limits = packageLimits || {};
    var total = files.reduce(function (sum, file) {
      return sum + file.size;
    }, 0);
    if (limits.files && files.length > limits.files) {
      say("assistants-status", name + " holds " + files.length + " files, and a solution holds " + limits.files +
        " at most. Choose the solution's own folder.", true);
      return;
    }
    if (limits.bytes && total > limits.bytes) {
      say("assistants-status", name + " holds more than " + Math.round(limits.bytes / 1048576) +
        " MB, more than a solution holds. Choose the solution's own folder.", true);
      return;
    }
    checkPackage(Promise.all(files.map(function (file) {
      return file.arrayBuffer().then(function (data) {
        return { path: file.webkitRelativePath || file.name, data: base64(data) };
      });
    })).then(function (entries) {
      return api("POST", "/api/installs", { files: entries });
    }), name);
  });

  function installSection(title) {
    var section = element("section", "install-section");
    section.append(element("h3", "subhead", title));
    byId("install-sections").append(section);
    return section;
  }

  function keyRow(key) {
    var row = element("div", "install-key");
    var head = element("p", "install-key-name");
    head.append(element("code", "", key.name), " read by " + key.reads_it);
    row.append(head);
    if (key.purpose) {
      row.append(element("p", "", key.purpose));
    }
    if (key.signup_url) {
      var link = element("a", "", "Where to get one");
      link.href = key.signup_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      var where = element("p", "hint");
      where.append(link);
      row.append(where);
    } else if (key.server && !key.note) {
      row.append(element("p", "hint", "The solution does not say where to get one."));
    }
    if (key.note) {
      row.append(element("p", "hint", key.note));
    }
    if (key.server && !(settings && settings.keys && settings.keys.locked)) {
      var id = "install-key-" + key.server + "-" + key.name;
      var field = element("div", "field");
      var label = element("label", "", "Its value");
      label.htmlFor = id;
      var input = element("input");
      input.type = "password";
      input.id = id;
      input.autocomplete = "off";
      input.spellcheck = false;
      input.placeholder = "Or give it later, under Tools";
      input.setAttribute("data-server", key.server);
      input.setAttribute("data-variable", key.name);
      field.append(label, input);
      row.append(field);
    }
    return row;
  }

  function drawInstall(screen) {
    installing = screen;
    var title = byId("install-title");
    title.textContent = "Install " + screen.title + "?";
    if (screen.unverified) {
      title.append(" ", element("span", "badge unverified", "Unverified"));
    }
    byId("install-description").textContent = screen.description;
    var notices = byId("install-notices");
    notices.replaceChildren();
    screen.notices.forEach(function (notice, index) {
      notices.append(element("p", "notice-card" + (index === 0 && screen.unverified ? " unverified-notice" : ""), notice));
    });
    byId("install-sections").replaceChildren();
    var provider = settings ? providerById(settings.provider) : null;
    var keys = installSection("Keys it needs");
    keys.append(element("p", "", "Your AI provider's key, from Settings" +
      (provider ? ": " + provider.name + "." : ". Choose a provider there first.")));
    screen.keys.forEach(function (key) {
      keys.append(keyRow(key));
    });
    if (settings && settings.keys && settings.keys.locked && screen.keys.some(function (key) { return key.server; })) {
      keys.append(element("p", "hint", "Your keys are locked. Unlock them in Settings to give a tool server its keys."));
    }
    if (screen.knowledge.length) {
      var documents = installSection("Documents it asks you for");
      screen.knowledge.forEach(function (slot) {
        var row = element("div", "slot");
        row.append(element("p", "slot-title", slot.name));
        row.append(element("p", "", slot.description));
        row.append(element("p", "hint", (slot.required ? "Needed before it runs. " : "") + "File types: " +
          slot.accepts.join(", ") + "." + (slot.sample ? " A made-up example comes with it." : "")));
        documents.append(row);
      });
    }
    if (screen.tools.length) {
      var tools = installSection("Tools");
      screen.tools.forEach(function (server) {
        var card = element("div", "tool-server");
        card.append(element("h4", "", server.name));
        card.append(element("p", "hint", (server.remote ? "A remote server at " : "Starts with: ") + server.runs));
        if (server.tools) {
          var list = element("ul", "tool-list");
          server.tools.forEach(function (tool) {
            list.append(element("li", "", tool.name + (tool.asks ? ": changes something, so it asks first" : ": reads, so it runs without asking")));
          });
          card.append(list);
        } else {
          card.append(element("p", "hint", "It declares none of its tools, so each asks before it runs."));
        }
        tools.append(card);
      });
    }
    if (screen.apps.length) {
      var apps = installSection("Apps");
      screen.apps.forEach(function (app) {
        var row = element("div", "app-entry");
        row.append(element("p", "slot-title", app.title));
        if (app.summary) {
          row.append(element("p", "", app.summary));
        }
        row.append(element("p", "hint", "Runs in an environment of its own: about " + app.download_mb +
          " MB to download once you say yes, and about " + app.disk_mb + " MB on disk."));
        if (app.provider_note) {
          row.append(element("p", "hint", app.provider_note));
        }
        apps.append(row);
      });
    }
    if (screen.skills.length || screen.skills_sold_separately.length) {
      var skills = installSection("Skills");
      screen.skills.forEach(function (skill) {
        skills.append(element("p", "", skill.name + ": " + skill.description));
      });
      if (screen.skills_sold_separately.length) {
        skills.append(element("p", "hint", "Sold separately, and it works without them: " + screen.skills_sold_separately.join(", ") + "."));
      }
    }
    if (screen.left_out.length) {
      var left = installSection("Left out");
      left.append(element("p", "hint", screen.left_out.join(", ")));
    }
    say("install-status", "", false);
    byId("install-card").hidden = false;
    byId("install-card").scrollIntoView({ block: "start" });
  }

  function closeInstall() {
    installing = null;
    byId("install-card").hidden = true;
  }

  byId("install-cancel").addEventListener("click", function () {
    if (installing) {
      api("DELETE", "/api/installs/" + encodeURIComponent(installing.id)).catch(function () {
        return null;
      });
    }
    closeInstall();
    say("assistants-status", "Nothing was installed.", false);
  });

  byId("install-confirm").addEventListener("click", function () {
    if (!installing) {
      return;
    }
    var screen = installing;
    var given = Array.prototype.slice.call(document.querySelectorAll("#install-sections input[data-variable]"))
      .filter(function (input) {
        return input.value.trim();
      })
      .map(function (input) {
        return { server: input.getAttribute("data-server"), name: input.getAttribute("data-variable"), value: input.value };
      });
    say("install-status", "Installing…", false);
    api("POST", "/api/installs/" + encodeURIComponent(screen.id))
      .then(function (view) {
        closeInstall();
        adoptAssistants(view);
        drawAssistantPick();
        drawAssistantList();
        say("assistants-status", "Installed " + screen.title + ".", false);
        var saved = given.reduce(function (chain, key) {
          return chain.then(function () {
            return api("PUT", "/api/assistants/" + encodeURIComponent(view.id) + "/tools/" +
              encodeURIComponent(key.server) + "/variables/" + encodeURIComponent(key.name), { value: key.value });
          });
        }, Promise.resolve(null));
        return saved
          .catch(function (error) {
            say("assistants-status", "Installed " + screen.title + ", but a key was not saved: " + error.message +
              " Give it under Tools.", true);
          })
          .then(function () {
            var made = chat.assistants.assistants.filter(function (assistant) {
              return assistant.id === view.id;
            })[0] || {};
            return made.apps && made.apps.length ? openApps(view.id) : null;
          })
          .then(function () {
            return view.asks_for_documents ? openKnowledge(view.id) : null;
          });
      })
      .catch(function (error) {
        say("install-status", error.message, true);
      });
  });

  function exportAssistant(id) {
    request("GET", "/api/assistants/" + encodeURIComponent(id) + "/export")
      .then(function (response) {
        if (!response.ok) {
          return refusal(response);
        }
        var disposition = response.headers.get("Content-Disposition") || "";
        var named = /filename="([^"]+)"/.exec(disposition);
        return response.blob().then(function (blob) {
          var link = element("a");
          link.href = URL.createObjectURL(blob);
          link.download = named ? named[1] : "assistant.zip";
          document.body.append(link);
          link.click();
          link.remove();
          setTimeout(function () {
            URL.revokeObjectURL(link.href);
          }, 1000);
          say("assistants-status", "Exported " + link.download + ".", false);
        });
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function deleteAssistant(assistant) {
    if (!window.confirm("Delete " + assistant.title + " and every conversation with it?")) {
      return;
    }
    api("DELETE", "/api/assistants/" + encodeURIComponent(assistant.id))
      .then(function (view) {
        adoptAssistants(view);
        if (editing === assistant.id) {
          closeEditor();
        }
        if (appsOf === assistant.id) {
          closeApps();
        }
        if (toolsOf === assistant.id) {
          closeTools();
        }
        if (knowledgeOf === assistant.id) {
          closeKnowledge();
        }
        drawAssistantPick();
        drawAssistantList();
        say("assistants-status", "Deleted " + assistant.title + ".", false);
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function skillEditor(skill) {
    var box = element("fieldset", "skill-edit");
    box.append(element("legend", "", skill.name || "New skill"));
    var name = labelled(box, "Name", "input");
    name.value = skill.name || "";
    name.className = "skill-name";
    var description = labelled(box, "When to load it", "input");
    description.value = skill.description || "";
    description.className = "skill-description";
    var body = labelled(box, "Its instructions", "textarea");
    body.rows = 6;
    body.value = skill.body || "";
    body.className = "skill-body";
    if (skill.references && skill.references.length) {
      box.append(element("p", "hint", "Reference files, kept while the skill keeps its name: " + skill.references.join(", ")));
    }
    box.append(button("Remove this skill", "quiet", function () {
      box.remove();
    }));
    return box;
  }

  function labelled(box, label, tag) {
    var field = element("div", "field");
    var id = "skill-field-" + Math.random().toString(36).slice(2);
    var caption = element("label", "", label);
    caption.htmlFor = id;
    var control = element(tag);
    control.id = id;
    if (tag === "input") {
      control.type = "text";
      control.autocomplete = "off";
    }
    field.append(caption, control);
    box.append(field);
    return control;
  }

  function editAssistant(id) {
    return api("GET", "/api/assistants/" + encodeURIComponent(id))
      .then(function (form) {
        editing = form.id;
        byId("assistant-form-title").textContent = "Edit " + form.title;
        byId("assistant-name").value = form.title;
        byId("assistant-description").value = form.description;
        byId("assistant-instructions").value = form.instructions;
        byId("assistant-persona").value = form.persona;
        byId("assistant-starters").value = form.starters.join("\n");
        byId("assistant-grounded").checked = Boolean(form.grounded_only);
        var skills = byId("skill-list");
        skills.replaceChildren();
        form.skills.forEach(function (skill) {
          skills.append(skillEditor(skill));
        });
        say("assistant-form-status", "", false);
        byId("assistant-form").hidden = false;
        byId("assistant-form").scrollIntoView({ block: "start" });
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function closeEditor() {
    editing = null;
    byId("assistant-form").hidden = true;
  }

  byId("add-skill").addEventListener("click", function () {
    byId("skill-list").append(skillEditor({ name: "", description: "", body: "" }));
  });

  byId("close-assistant").addEventListener("click", closeEditor);

  byId("assistant-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var skills = [];
    var editors = byId("skill-list").querySelectorAll(".skill-edit");
    for (var index = 0; index < editors.length; index += 1) {
      skills.push({
        name: editors[index].querySelector(".skill-name").value,
        description: editors[index].querySelector(".skill-description").value,
        body: editors[index].querySelector(".skill-body").value,
      });
    }
    var form = {
      title: byId("assistant-name").value,
      description: byId("assistant-description").value,
      instructions: byId("assistant-instructions").value,
      persona: byId("assistant-persona").value,
      starters: byId("assistant-starters").value.split("\n"),
      grounded_only: byId("assistant-grounded").checked,
      skills: skills,
    };
    api("PUT", "/api/assistants/" + encodeURIComponent(editing), form)
      .then(function (saved) {
        byId("assistant-form-title").textContent = "Edit " + saved.title;
        say("assistant-form-status", "Saved.", false);
        return api("GET", "/api/assistants");
      })
      .then(function (view) {
        adoptAssistants(view);
        drawAssistantPick();
        drawAssistantList();
      })
      .catch(function (error) {
        say("assistant-form-status", error.message, true);
      });
  });

  // Documents -----------------------------------------------------------------

  function knowledgePath(id) {
    return "/api/assistants/" + encodeURIComponent(id) + "/knowledge";
  }

  function openKnowledge(id) {
    return api("GET", knowledgePath(id))
      .then(drawKnowledge)
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function closeKnowledge() {
    knowledgeOf = null;
    byId("knowledge-card").hidden = true;
  }

  function knowledgeChange(promise, done) {
    return promise
      .then(function (panel) {
        drawKnowledge(panel);
        say("knowledge-status", done, false);
      })
      .catch(function (error) {
        say("knowledge-status", error.message, true);
      });
  }

  function upload(file, query) {
    return file.arrayBuffer().then(function (data) {
      return api("POST", knowledgePath(knowledgeOf) + query, data, "application/octet-stream");
    });
  }

  function modeWords(panel) {
    if (panel.mode === "whole") {
      return "These go into each message whole: about " + panel.tokens.toLocaleString() + " tokens of the " +
        panel.limit.toLocaleString() + " Bailey sends whole, so the assistant reads all of them. It cites each passage it uses.";
    }
    if (panel.mode === "search") {
      return "These are about " + panel.tokens.toLocaleString() + " tokens, more than the " + panel.limit.toLocaleString() +
        " Bailey sends whole, so the assistant searches them for each question. It cites each passage it uses.";
    }
    return "Give the assistant documents, and it answers from them, citing each passage it uses. They stay on this computer.";
  }

  function drawKnowledge(panel) {
    knowledgeOf = panel.id;
    byId("knowledge-title").textContent = "Documents of " + assistantTitle(panel.id);
    byId("knowledge-mode").textContent = modeWords(panel);
    var needsModel = panel.mode === "search" && !panel.model.ready;
    byId("knowledge-model").hidden = !needsModel;
    byId("knowledge-model-text").textContent =
      "Bailey searches documents with a model it downloads once from " + panel.model.source + ": " + panel.model.label +
      ", about " + panel.model.size_mb + " MB. Your documents stay on this computer.";
    var slots = byId("knowledge-slots");
    slots.replaceChildren();
    if (panel.slots.length) {
      slots.append(element("h3", "subhead", "What this solution asks for"));
    }
    panel.slots.forEach(function (slot) {
      var row = element("div", "slot");
      var title = element("p", "slot-title", slot.description);
      if (slot.required) {
        title.append(" ", element("span", "badge", "Needed"));
      }
      row.append(title);
      var held = slot.document;
      row.append(
        element(
          "p",
          "hint",
          held
            ? (held.kind === "sample" ? "The sample, " : "Your file, ") + held.name + "."
            : "Not given yet. Bailey takes " + slot.accepts.join(", ") + "."
        )
      );
      var actions = element("div", "actions");
      var id = "slot-file-" + slot.name;
      var pick = element("label", "file-button", held && held.kind !== "sample" ? "Replace your file" : "Add your file");
      pick.htmlFor = id;
      var input = element("input");
      input.type = "file";
      input.id = id;
      input.className = "sr-only";
      input.accept = slot.accepts.map(function (extension) {
        return "." + extension;
      }).join(",");
      input.addEventListener("change", function () {
        var file = input.files && input.files[0];
        input.value = "";
        if (file) {
          knowledgeChange(upload(file, "?slot=" + encodeURIComponent(slot.name) + "&name=" + encodeURIComponent(file.name)), "Added " + file.name + ".");
        }
      });
      actions.append(pick, input);
      if (slot.sample && !(held && held.kind === "sample")) {
        actions.append(button("Use the sample", "quiet", function () {
          knowledgeChange(api("POST", knowledgePath(panel.id) + "/samples/" + encodeURIComponent(slot.name)), "Using the sample.");
        }));
      }
      if (held) {
        actions.append(button("Remove", "quiet", function () {
          knowledgeChange(api("DELETE", knowledgePath(panel.id) + "/" + encodeURIComponent(held.id)), "Removed " + held.name + ".");
        }));
      }
      row.append(actions);
      slots.append(row);
    });
    var list = byId("knowledge-documents");
    list.replaceChildren();
    if (!panel.documents.length) {
      list.append(element("li", "hint", "None yet."));
    }
    panel.documents.forEach(function (document) {
      var item = element("li", "document");
      item.append(element("span", "", document.name + (document.kind === "reference" ? " (from the solution, searched when needed)" : "")));
      item.append(button("Remove", "quiet", function () {
        knowledgeChange(api("DELETE", knowledgePath(panel.id) + "/" + encodeURIComponent(document.id)), "Removed " + document.name + ".");
      }));
      list.append(item);
    });
    byId("knowledge-file").accept = panel.accepts.map(function (extension) {
      return "." + extension;
    }).join(",");
    byId("knowledge-card").hidden = false;
    byId("knowledge-card").scrollIntoView({ block: "start" });
  }

  byId("knowledge-file").addEventListener("change", function (event) {
    var file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (file && knowledgeOf) {
      knowledgeChange(upload(file, "?name=" + encodeURIComponent(file.name)), "Added " + file.name + ".");
    }
  });

  byId("knowledge-download").addEventListener("click", function () {
    var buttonEl = byId("knowledge-download");
    buttonEl.disabled = true;
    say("knowledge-status", "Downloading the search model\u2026", false);
    api("POST", "/api/knowledge/model")
      .then(function () {
        return openKnowledge(knowledgeOf);
      })
      .then(function () {
        say("knowledge-status", "The search model is here. Bailey will not fetch it again.", false);
      })
      .catch(function (error) {
        say("knowledge-status", error.message, true);
      })
      .then(function () {
        buttonEl.disabled = false;
      });
  });

  byId("close-knowledge").addEventListener("click", closeKnowledge);

  // Tools ---------------------------------------------------------------------

  function assistantTitle(id) {
    var found = (chat.assistants ? chat.assistants.assistants : []).filter(function (assistant) {
      return assistant.id === id;
    });
    return found.length ? found[0].title : "this assistant";
  }

  // -- apps: each a crew its own runner serves, drawn by Gatehouse through Bailey -----------

  var appsOf = null;
  var appPoll = null;
  var appRenderer = null;

  function appsPath(id) {
    return "/api/assistants/" + encodeURIComponent(id) + "/apps";
  }

  function openApps(id) {
    return api("GET", appsPath(id))
      .then(drawApps)
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function closeApps() {
    appsOf = null;
    stopAppPoll();
    closeAppRun();
    byId("apps-card").hidden = true;
  }

  function drawApps(panel) {
    if (appsOf !== panel.id) {
      closeAppRun();
    }
    appsOf = panel.id;
    byId("apps-title").textContent = "Apps of " + assistantTitle(panel.id);
    byId("apps-notice").textContent = panel.notice;
    var list = byId("apps-list");
    list.replaceChildren();
    var preparing = false;
    panel.apps.forEach(function (app) {
      var entry = element("div", "app-entry");
      entry.append(element("h3", "", app.title));
      if (app.summary) {
        entry.append(element("p", "", app.summary));
      }
      if (app.provider_note) {
        entry.append(element("p", "hint", app.provider_note));
      }
      var actions = element("div", "actions");
      if (app.environment === "ready") {
        var open = button("Open", "", function () {
          openAppRun(panel.id, app);
        });
        open.disabled = Boolean(app.provider_note);
        actions.append(open);
      } else if (app.environment === "preparing") {
        preparing = true;
        entry.append(element("p", "hint", "Installing its libraries. The first time takes a few minutes."));
      } else {
        entry.append(element("p", "hint", (app.environment === "failed" && app.why ? app.why + " " : "") +
          "Bailey runs each app in an environment of its own, with CrewAI and its libraries: about " +
          panel.download_mb + " MB, downloaded once from PyPI, and about " + panel.disk_mb + " MB on disk."));
        actions.append(button(app.environment === "failed" ? "Try again" : "Install its libraries", "", function () {
          api("POST", appsPath(panel.id) + "/" + encodeURIComponent(app.name) + "/environment")
            .then(drawApps)
            .catch(function (error) {
              say("apps-status", error.message, true);
            });
        }));
      }
      entry.append(actions);
      list.append(entry);
    });
    if (preparing) {
      startAppPoll(panel.id);
    } else {
      stopAppPoll();
    }
    byId("apps-card").hidden = false;
    byId("apps-card").scrollIntoView({ block: "start" });
  }

  function startAppPoll(id) {
    if (appPoll) {
      return;
    }
    appPoll = window.setInterval(function () {
      api("GET", appsPath(id))
        .then(function (panel) {
          if (appsOf === id) {
            drawApps(panel);
          }
        })
        .catch(function () {
          stopAppPoll();
        });
    }, 3000);
  }

  function stopAppPoll() {
    if (appPoll) {
      window.clearInterval(appPoll);
      appPoll = null;
    }
  }

  // Gatehouse draws the form from the app's describe, lays it out from its ui.json, and
  // shows the steps as they happen. Every request goes through Bailey with the session's token.
  function openAppRun(id, app) {
    closeAppRun();
    var box = byId("app-run");
    var mount = element("div", "app-mount");
    box.append(mount);
    var client = new window.Gatehouse.PosternClient(appsPath(id) + "/" + encodeURIComponent(app.name), {
      headers: { Authorization: "Bearer " + token },
    });
    appRenderer = new window.Gatehouse.RunRenderer(mount, client, { composition: app.ui || null });
    box.hidden = false;
    appRenderer.load().catch(function (error) {
      say("apps-status", (error && error.message) || "The app did not open.", true);
    });
    box.scrollIntoView({ block: "start" });
  }

  function closeAppRun() {
    if (appRenderer) {
      appRenderer.stop();
      appRenderer = null;
    }
    var box = byId("app-run");
    box.replaceChildren();
    box.hidden = true;
  }

  byId("close-apps").addEventListener("click", closeApps);

  function openTools(id) {
    return api("GET", "/api/assistants/" + encodeURIComponent(id) + "/tools")
      .then(drawTools)
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  }

  function closeTools() {
    toolsOf = null;
    byId("tools-card").hidden = true;
  }

  function drawTools(panel) {
    toolsOf = panel.id;
    byId("tools-title").textContent = "Tools of " + assistantTitle(panel.id);
    byId("tools-notice").textContent = panel.notice;
    var box = byId("tools-servers");
    box.replaceChildren();
    if (panel.locked) {
      box.append(element("p", "hint", "Your keys are locked. Unlock them in Settings to give a server its values."));
    }
    panel.servers.forEach(function (server) {
      var card = element("div", "tool-server");
      card.append(element("h3", "", server.name));
      if (server.remote) {
        card.append(element("p", "hint", "A remote server at " + server.launch + ". This version of Bailey does not connect to remote servers."));
        box.append(card);
        return;
      }
      card.append(element("p", "hint", "Starts with: " + server.launch));
      if (server.declared) {
        var tools = element("ul", "tool-list");
        server.tools.forEach(function (tool) {
          tools.append(element("li", "", tool.name + (tool.writes ? ": changes something, so it asks first" : ": reads, so it runs without asking")));
        });
        card.append(tools);
        card.append(element("p", "hint", "A tool this list leaves out asks before it runs."));
      } else {
        card.append(element("p", "hint", "Its solution declares none of its tools, so each asks before it runs."));
      }
      server.variables.forEach(function (variable) {
        card.append(variableRow(panel.id, server.name, variable));
      });
      box.append(card);
    });
    byId("tools-card").hidden = false;
    byId("tools-card").scrollIntoView({ block: "start" });
  }

  function variableRow(assistantId, serverName, variable) {
    var row = element("form", "variable");
    row.noValidate = true;
    var field = element("div", "field");
    var id = "variable-" + serverName + "-" + variable.name;
    var caption = element("label", "", variable.name + (variable.shared ? ", your Sigrix token, shared by every server Sigrix delivers" : ""));
    caption.htmlFor = id;
    var input = element("input");
    input.type = "password";
    input.id = id;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = variable.saved ? "Saved. Type a new value to replace it." : "Not given yet";
    field.append(caption, input);
    var actions = element("div", "actions");
    var save = element("button", "", "Save");
    save.type = "submit";
    actions.append(save);
    var path = "/api/assistants/" + encodeURIComponent(assistantId) + "/tools/" + encodeURIComponent(serverName) +
      "/variables/" + encodeURIComponent(variable.name);
    if (variable.saved) {
      actions.append(button("Remove", "quiet", function () {
        api("DELETE", path)
          .then(function (panel) {
            drawTools(panel);
            say("tools-status", "Removed " + variable.name + ".", false);
          })
          .catch(function (error) {
            say("tools-status", error.message, true);
          });
      }));
    }
    row.append(field, actions);
    row.addEventListener("submit", function (event) {
      event.preventDefault();
      api("PUT", path, { value: input.value })
        .then(function (panel) {
          drawTools(panel);
          say("tools-status", "Saved " + variable.name + " in " + (settings && settings.keys ? settings.keys.where : "your keys") + ".", false);
        })
        .catch(function (error) {
          say("tools-status", error.message, true);
        });
    });
    return row;
  }

  byId("close-tools").addEventListener("click", closeTools);

  function drawGrants(view) {
    var list = byId("grant-list");
    list.replaceChildren();
    if (!view.grants.length) {
      list.append(element("li", "hint", "None. Every tool that changes something asks before it runs."));
      return;
    }
    view.grants.forEach(function (grant) {
      var item = element("li", "grant");
      item.append(element("span", "", grant.tool + " on " + grant.server + ", for " + (grant.assistant_title || "an assistant")));
      item.append(button("Ask again", "quiet", function () {
        api("POST", "/api/grants/revoke", { assistant: grant.assistant_id, server: grant.server, tool: grant.tool })
          .then(function (answer) {
            drawGrants(answer);
            say("grants-status", grant.tool + " asks again before it runs.", false);
          })
          .catch(function (error) {
            say("grants-status", error.message, true);
          });
      }));
      list.append(item);
    });
  }

  function loadGrants() {
    return api("GET", "/api/grants").then(drawGrants);
  }

  // Settings ------------------------------------------------------------------

  function providerById(id) {
    if (!settings) {
      return null;
    }
    for (var index = 0; index < settings.providers.length; index += 1) {
      if (settings.providers[index].id === id) {
        return settings.providers[index];
      }
    }
    return null;
  }

  function selectedProvider() {
    if (!settings) {
      return null;
    }
    var checked = document.querySelector('input[name="provider"]:checked');
    return providerById(checked ? checked.value : settings.provider);
  }

  function drawProviders() {
    var choices = byId("provider-choices");
    choices.replaceChildren();
    settings.providers.forEach(function (provider) {
      var label = element("label", "choice");
      var input = element("input");
      input.type = "radio";
      input.name = "provider";
      input.value = provider.id;
      input.checked = provider.id === settings.provider;
      input.addEventListener("change", drawSelection);
      label.append(input, element("span", "", provider.name));
      choices.append(label);
    });
    byId("address").value = settings.address || "";
  }

  function drawSelection() {
    var provider = selectedProvider();
    byId("address-field").hidden = !(provider && provider.needs_address);
    byId("model-field").hidden = !provider;
    if (provider) {
      var model = byId("model");
      model.value = settings.models[provider.id] || "";
      model.placeholder = provider.default_model || "the model's name, as your server lists it";
      byId("model-hint").textContent = provider.default_model
        ? "Bailey starts with " + provider.default_model + "; any model your account can use works."
        : "As your server lists it, such as qwen3:8b.";
    }
    drawKeys(provider);
  }

  function drawKeys(provider) {
    var keys = settings.keys;
    byId("keys-where").textContent = "Keys are kept in " + keys.where + ".";
    var locked = keys.locked;
    byId("locked-card").hidden = !locked;
    byId("key-card").hidden = locked || !provider;
    if (locked) {
      drawLocked(keys);
      return;
    }
    if (!provider) {
      return;
    }
    byId("key-title").textContent = "Key for " + provider.name;
    var saved = provider.key && provider.key.saved;
    if (saved) {
      byId("key-state").textContent = "A key is saved (" + provider.key.hint + "). A new one replaces it.";
    } else if (provider.key_required) {
      byId("key-state").textContent = "No key saved yet. Bailey needs one to talk to " + provider.name + ".";
    } else {
      byId("key-state").textContent = "No key saved. A local server often needs none; save one if yours does.";
    }
    byId("key-remove").hidden = !saved;
    var link = byId("key-link");
    var hasLink = typeof provider.key_url === "string" && provider.key_url.indexOf("https://") === 0;
    byId("key-help").hidden = !hasLink;
    if (hasLink) {
      link.href = provider.key_url;
    } else {
      link.removeAttribute("href");
    }
  }

  function drawLocked(keys) {
    var chosen = Boolean(keys.has_passphrase);
    byId("unlock-form").hidden = !chosen;
    byId("choose-form").hidden = chosen;
    byId("locked-explain").textContent = chosen
      ? "This computer has no keychain, so Bailey keeps your keys in " +
        keys.where +
        ". Type its passphrase to open it for this session."
      : "This computer has no keychain, so Bailey keeps your keys in an encrypted file. Choose a passphrase " +
        "for it; Bailey asks for it each time it starts. Nobody can read the file without it, Bailey included, " +
        "so a forgotten passphrase means entering your keys again.";
  }

  function drawSettings(answer) {
    settings = answer;
    drawProviders();
    drawSelection();
    drawSetupNeeded();
    var installingSetting = answer.installing || {};
    byId("skills-with-scripts").checked = Boolean(installingSetting.skills_with_scripts);
    byId("skills-with-scripts-label").textContent = installingSetting.label || "";
  }

  byId("skills-with-scripts").addEventListener("change", function (event) {
    var on = event.target.checked;
    api("PUT", "/api/settings", { skills_with_scripts: on })
      .then(function (answer) {
        drawSettings(answer);
        say("installing-status", on ? "Skills with scripts install from a file, without their scripts." :
          "Skills with scripts install only from the Sigrix store.", false);
      })
      .catch(function (error) {
        event.target.checked = !on;
        say("installing-status", error.message, true);
      });
  });

  function loadSettings() {
    return api("GET", "/api/settings").then(drawSettings);
  }

  byId("provider-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var provider = selectedProvider();
    if (!provider) {
      say("provider-status", "Choose a provider first.", true);
      return;
    }
    api("PUT", "/api/settings", { provider: provider.id, address: byId("address").value, model: byId("model").value })
      .then(function (answer) {
        drawSettings(answer);
        say("provider-status", "Saved. Bailey will talk to " + provider.name + ".", false);
      })
      .catch(function (error) {
        say("provider-status", error.message, true);
      });
  });

  byId("key-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var provider = selectedProvider();
    var field = byId("key");
    api("PUT", "/api/keys/" + encodeURIComponent(provider.id), { key: field.value })
      .then(function (answer) {
        field.value = "";
        drawSettings(answer);
        say("key-status", "Saved in " + answer.keys.where + ".", false);
      })
      .catch(function (error) {
        say("key-status", error.message, true);
      });
  });

  byId("key-remove").addEventListener("click", function () {
    var provider = selectedProvider();
    api("DELETE", "/api/keys/" + encodeURIComponent(provider.id))
      .then(function (answer) {
        drawSettings(answer);
        say("key-status", "Removed.", false);
      })
      .catch(function (error) {
        say("key-status", error.message, true);
      });
  });

  byId("unlock-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var field = byId("unlock-passphrase");
    say("locked-status", "Opening…", false);
    api("POST", "/api/keys/unlock", { passphrase: field.value })
      .then(function (answer) {
        field.value = "";
        say("locked-status", "", false);
        drawSettings(answer);
      })
      .catch(function (error) {
        say("locked-status", error.message, true);
      });
  });

  byId("choose-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var first = byId("new-passphrase");
    var second = byId("new-passphrase-again");
    if (first.value !== second.value) {
      say("locked-status", "The two passphrases differ. Type the same one twice.", true);
      return;
    }
    api("POST", "/api/keys/passphrase", { passphrase: first.value })
      .then(function (answer) {
        first.value = "";
        second.value = "";
        say("locked-status", "", false);
        drawSettings(answer);
      })
      .catch(function (error) {
        say("locked-status", error.message, true);
      });
  });

  byId("reset-keys").addEventListener("click", function () {
    var sure = window.confirm(
      "Delete the key file and every key in it? You will choose a new passphrase and enter your keys again."
    );
    if (!sure) {
      return;
    }
    api("POST", "/api/keys/reset")
      .then(function (answer) {
        say("locked-status", "The key file is gone. Choose a new passphrase.", false);
        drawSettings(answer);
      })
      .catch(function (error) {
        say("locked-status", error.message, true);
      });
  });

  // Start ---------------------------------------------------------------------

  var views = document.querySelectorAll("[data-view]");
  for (var index = 0; index < views.length; index += 1) {
    views[index].addEventListener("click", function (event) {
      openView(event.currentTarget.getAttribute("data-view"));
    });
  }

  function begin() {
    if (!token) {
      showOnly(byId("no-session"));
      return;
    }
    byId("no-session").hidden = true;
    byId("loading").hidden = false;
    api("GET", "/api/status")
      .then(function (status) {
        byId("version").textContent = status.version;
        packageLimits = status.package;
        byId("nav").hidden = false;
        return loadSettings();
      })
      .then(function () {
        openView("chat");
      })
      .catch(function (error) {
        if (token) {
          byId("loading").textContent = error.message;
        }
      });
  }

  // Opening the launch address in a tab already on this page changes only its
  // fragment, which reloads nothing; the new token is taken from it here.
  window.addEventListener("hashchange", function () {
    var fresh = takeToken();
    if (fresh && fresh !== token) {
      token = fresh;
      begin();
    }
  });

  begin();
})();
