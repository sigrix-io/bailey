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

  function byId(id) {
    return document.getElementById(id);
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

  function showOnly(element) {
    byId("loading").hidden = true;
    byId("no-session").hidden = element !== byId("no-session");
    var panels = document.querySelectorAll("[data-panel]");
    for (var index = 0; index < panels.length; index += 1) {
      panels[index].hidden = panels[index] !== element;
    }
  }

  function lostSession() {
    token = "";
    store("");
    byId("nav").hidden = true;
    showOnly(byId("no-session"));
  }

  function api(method, path, body) {
    var options = { method: method, headers: { Authorization: "Bearer " + token } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (response) {
      if (response.status === 401) {
        lostSession();
        throw new Error("This session has ended.");
      }
      return response.json().then(
        function (answer) {
          if (!response.ok) {
            throw new Error(answer && answer.error ? answer.error : "Bailey could not do that.");
          }
          return answer;
        },
        function () {
          throw new Error("Bailey answered with something this page cannot read.");
        }
      );
    });
  }

  function say(id, text, isError) {
    var element = byId(id);
    element.textContent = text;
    element.classList.toggle("error", Boolean(isError));
  }

  // Views -----------------------------------------------------------------

  function openView(name) {
    var buttons = document.querySelectorAll("[data-view]");
    for (var index = 0; index < buttons.length; index += 1) {
      var current = buttons[index].getAttribute("data-view") === name;
      if (current) {
        buttons[index].setAttribute("aria-current", "page");
      } else {
        buttons[index].removeAttribute("aria-current");
      }
    }
    showOnly(document.querySelector('[data-panel="' + name + '"]'));
  }

  // Settings ----------------------------------------------------------------

  function selectedProvider() {
    if (!settings) {
      return null;
    }
    var checked = document.querySelector('input[name="provider"]:checked');
    var id = checked ? checked.value : settings.provider;
    for (var index = 0; index < settings.providers.length; index += 1) {
      if (settings.providers[index].id === id) {
        return settings.providers[index];
      }
    }
    return null;
  }

  function drawProviders() {
    var choices = byId("provider-choices");
    choices.replaceChildren();
    settings.providers.forEach(function (provider) {
      var label = document.createElement("label");
      label.className = "choice";
      var input = document.createElement("input");
      input.type = "radio";
      input.name = "provider";
      input.value = provider.id;
      input.checked = provider.id === settings.provider;
      input.addEventListener("change", drawSelection);
      var name = document.createElement("span");
      name.textContent = provider.name;
      label.append(input, name);
      choices.append(label);
    });
    byId("address").value = settings.address || "";
  }

  function drawSelection() {
    var provider = selectedProvider();
    byId("address-field").hidden = !(provider && provider.needs_address);
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
  }

  function loadSettings() {
    return api("GET", "/api/settings").then(drawSettings);
  }

  // Forms ---------------------------------------------------------------------

  byId("provider-form").addEventListener("submit", function (event) {
    event.preventDefault();
    var provider = selectedProvider();
    if (!provider) {
      say("provider-status", "Choose a provider first.", true);
      return;
    }
    api("PUT", "/api/settings", { provider: provider.id, address: byId("address").value })
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

  var buttons = document.querySelectorAll("[data-view]");
  for (var index = 0; index < buttons.length; index += 1) {
    buttons[index].addEventListener("click", function (event) {
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
        openView("chat");
        return loadSettings();
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
