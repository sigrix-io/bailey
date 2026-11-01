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
  var chat = { assistants: null, conversation: null, streaming: false };
  var editing = null;

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

  function loadAssistants() {
    return api("GET", "/api/assistants").then(function (view) {
      chat.assistants = view;
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
    var text = element("p", "message-text", entry.text || "");
    var meta = element("p", "message-meta", entry.role === "assistant" ? metaLine(entry) : "");
    bubble.append(text, meta);
    byId("messages").append(bubble);
    bubble.scrollIntoView({ block: "end" });
    return { bubble: bubble, text: text, meta: meta };
  }

  function drawMessages(entries) {
    byId("messages").replaceChildren();
    entries.forEach(appendEntry);
    drawStarters(entries.length === 0);
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
    var skills = [];
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
        return readEvents(response, function (event) {
          if (event.type === "text") {
            reply.text.textContent += event.text;
            reply.meta.textContent = skills.length ? "Skill: " + skills.join(", ") : "";
          } else if (event.type === "skill") {
            if (skills.indexOf(event.name) === -1) {
              skills.push(event.name);
            }
            reply.meta.textContent = "Loading the " + event.name + " skill…";
          } else if (event.type === "done") {
            reply.meta.textContent = metaLine({ skills: skills, usage: event.usage, estimate: event.estimate });
            say("chat-status", "Answer finished.", false);
          } else if (event.type === "error") {
            reply.bubble.className = "message message-notice";
            reply.text.textContent = event.message;
            reply.meta.textContent = "";
          }
        });
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
        byId("send").disabled = false;
        return api("GET", "/api/conversations").then(function (answer) {
          drawConversationList(answer.conversations);
        });
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
        chat.assistants = view;
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
      words.append(title);
      if (assistant.description) {
        words.append(element("p", "hint", assistant.description));
      }
      words.append(element("p", "hint", assistant.skills.length ? "Skills: " + assistant.skills.join(", ") : "No skills"));
      var actions = element("div", "actions");
      if (assistant.id !== chat.assistants.active) {
        actions.append(button("Use", "", function () {
          useAssistant(assistant.id);
        }));
      }
      actions.append(button("Edit", "quiet", function () {
        editAssistant(assistant.id);
      }));
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
        chat.assistants = view;
        drawAssistantPick();
        drawAssistantList();
        say("assistants-status", "Made. Edit it below, then use it.", false);
        return editAssistant(view.id);
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
      });
  });

  byId("import-file").addEventListener("change", function (event) {
    var file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    file
      .arrayBuffer()
      .then(function (data) {
        return api("POST", "/api/assistants/import", data, "application/zip");
      })
      .then(function (view) {
        chat.assistants = view;
        drawAssistantPick();
        drawAssistantList();
        say("assistants-status", "Imported " + file.name + ".", false);
      })
      .catch(function (error) {
        say("assistants-status", error.message, true);
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
        chat.assistants = view;
        if (editing === assistant.id) {
          closeEditor();
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
      skills: skills,
    };
    api("PUT", "/api/assistants/" + encodeURIComponent(editing), form)
      .then(function (saved) {
        byId("assistant-form-title").textContent = "Edit " + saved.title;
        say("assistant-form-status", "Saved.", false);
        return api("GET", "/api/assistants");
      })
      .then(function (view) {
        chat.assistants = view;
        drawAssistantPick();
        drawAssistantList();
      })
      .catch(function (error) {
        say("assistant-form-status", error.message, true);
      });
  });

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
  }

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
