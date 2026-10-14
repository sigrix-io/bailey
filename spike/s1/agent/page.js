// The spike's page. Every piece of model output is written with textContent, never as markup.
(function () {
  "use strict";

  const token = document.querySelector('meta[name="bailey-session"]').content;
  const transcript = document.getElementById("transcript");
  const form = document.getElementById("ask");

  function api(path, body) {
    const options = { headers: { Authorization: "Bearer " + token } };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options);
  }

  function line(className, text) {
    const element = document.createElement("p");
    element.className = className;
    element.textContent = text;
    transcript.appendChild(element);
    return element;
  }

  function askApproval(runId, event) {
    const card = document.createElement("div");
    card.className = "approval";
    card.dataset.tool = event.tool;
    const question = document.createElement("p");
    question.textContent = "Allow the assistant to run " + event.tool + "?";
    const args = document.createElement("pre");
    args.textContent = JSON.stringify(event.args, null, 2);
    card.append(question, args);
    for (const [answer, label] of [["once", "Allow once"], ["always", "Always for this assistant"], ["no", "No"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.dataset.answer = answer;
      button.addEventListener("click", async () => {
        card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        await api("/api/runs/" + runId + "/approvals", { id: event.id, answer: answer });
        card.dataset.answered = answer;
      });
      card.appendChild(button);
    }
    transcript.appendChild(card);
  }

  async function follow(runId) {
    const response = await api("/api/runs/" + runId + "/events");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = "";
    let answer = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffered.indexOf("\n\n")) >= 0) {
        const frame = buffered.slice(0, end);
        buffered = buffered.slice(end + 2);
        if (!frame.startsWith("data: ")) continue;
        const event = JSON.parse(frame.slice(6));
        if (event.type === "text") {
          if (answer === null) answer = line("answer", "");
          answer.textContent += event.text;
        } else if (event.type === "tool_call") {
          line("tool", "Calling " + event.tool);
          answer = null;
        } else if (event.type === "tool_result") {
          line("tool", event.tool + " answered");
        } else if (event.type === "approval") {
          askApproval(runId, event);
        } else if (event.type === "approved_always") {
          line("tool", event.tool + " allowed: you chose always");
        } else if (event.type === "done") {
          line("usage", "Tokens: " + event.input_tokens + " in, " + event.output_tokens + " out (an estimate)");
          transcript.dataset.state = "done";
        } else if (event.type === "error") {
          line("error", event.message);
          transcript.dataset.state = "error";
        }
      }
    }
  }

  form.addEventListener("submit", async (submitted) => {
    submitted.preventDefault();
    const message = document.getElementById("message").value;
    transcript.dataset.state = "running";
    line("user", message);
    const response = await api("/api/runs", { provider: document.getElementById("provider").value, message: message });
    const started = await response.json();
    if (!response.ok) {
      line("error", started.error);
      transcript.dataset.state = "error";
      return;
    }
    await follow(started.run);
  });
})();
