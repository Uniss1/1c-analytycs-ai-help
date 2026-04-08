/**
 * 1C Analytics AI Help — embedded chat widget.
 * Injected into 1C Analytics pages via nginx sub_filter.
 */
(function () {
  'use strict';

  const API_BASE = '/assistant/api';
  const WIDGET_CSS = '/assistant/widget/widget.css';

  function getDashboardContext() {
    return {
      url: window.location.pathname,
      title: document.title,
    };
  }

  function init() {
    // Load CSS
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = WIDGET_CSS;
    document.head.appendChild(link);

    // Create widget button
    const btn = document.createElement('div');
    btn.id = 'ai-help-btn';
    btn.innerHTML = '💬';
    btn.title = 'AI Помощник';
    document.body.appendChild(btn);

    // Create chat panel (hidden by default)
    const panel = document.createElement('div');
    panel.id = 'ai-help-panel';
    panel.style.display = 'none';
    panel.innerHTML = `
      <div id="ai-help-header">
        <span>AI Помощник</span>
        <button id="ai-help-close">&times;</button>
      </div>
      <div id="ai-help-messages"></div>
      <div id="ai-help-input-area">
        <input id="ai-help-input" type="text" placeholder="Задайте вопрос..." />
        <button id="ai-help-send">→</button>
      </div>
    `;
    document.body.appendChild(panel);

    // Toggle panel
    btn.addEventListener('click', function () {
      panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    });

    document.getElementById('ai-help-close').addEventListener('click', function () {
      panel.style.display = 'none';
    });

    // Send message
    async function sendMessage() {
      const input = document.getElementById('ai-help-input');
      const message = input.value.trim();
      if (!message) return;

      appendMessage('user', message);
      input.value = '';

      try {
        const response = await fetch(API_BASE + '/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: message,
            dashboard_context: getDashboardContext(),
          }),
        });
        const data = await response.json();
        appendMessage('assistant', data.answer);
      } catch (err) {
        appendMessage('assistant', 'Ошибка: не удалось получить ответ');
      }
    }

    document.getElementById('ai-help-send').addEventListener('click', sendMessage);
    document.getElementById('ai-help-input').addEventListener('keypress', function (e) {
      if (e.key === 'Enter') sendMessage();
    });
  }

  function appendMessage(role, text) {
    const messages = document.getElementById('ai-help-messages');
    const div = document.createElement('div');
    div.className = 'ai-msg ai-msg-' + role;
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
