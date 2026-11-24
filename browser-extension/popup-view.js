/* Presentation only: real task state and commands remain in popup.js. */
(() => {
  const get = id => document.getElementById(id);
  const storageKey = 'xhs-panel-preferences';
  const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
  const colorQuery = matchMedia('(prefers-color-scheme: dark)');
  const panels = {
    'task-details': {button:'details-toggle', title:'读取详情'},
    advanced: {button:'settings-toggle', title:'名称与总数'},
    'help-panel': {button:'help-toggle', title:'使用帮助'}
  };
  let preferences = {appearance:'system', reduceEffects:false, glassOpacity:.72};
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || '{}');
    if (['system','light','dark'].includes(stored.appearance)) preferences.appearance = stored.appearance;
    if (typeof stored.reduceEffects === 'boolean') preferences.reduceEffects = stored.reduceEffects;
    if (typeof stored.glassOpacity === 'number' && Number.isFinite(stored.glassOpacity)) {
      preferences.glassOpacity = Math.max(.4, Math.min(1, stored.glassOpacity));
    }
  } catch { /* Browser storage may be unavailable; system defaults still work. */ }
  let current = null, closing = false, sequence = 0, trigger = null;
  let pendingFocusRestore = false;
  let sheetAnimation, backdropAnimation;
  let message = '', fieldError = '';
  const locations = new Map();
  const reduced = () => motionQuery.matches || preferences.reduceEffects;

  function icon(button, name) {
    const source = window.XHS_ICONS?.[name];
    if (!source || button.dataset.renderedIcon === name) return;
    let holder = button.querySelector(':scope > .ui-icon');
    if (!holder) {
      holder = document.createElement('span');
      holder.className = 'ui-icon';
      holder.setAttribute('aria-hidden','true');
      button.prepend(holder);
    }
    // Only vendored Lucide SVG constants are inserted, never task data.
    holder.innerHTML = source;
    button.dataset.renderedIcon = name;
  }

  function syncIcons() {
    for (const button of document.querySelectorAll('[data-icon]')) icon(button, button.dataset.icon);
    for (const id of ['start','resume']) {
      const button = get(id);
      if (!button) continue;
      const label = button.querySelector('[data-button-label]')?.textContent || button.textContent;
      icon(button, label.includes('更新') || label.includes('重试') ? 'rotate-cw' : 'play');
    }
    const refresh = get('refresh');
    refresh.setAttribute('aria-label', refresh.querySelector('[data-button-label]')?.textContent || '刷新');
  }

  function applyPreferences(save = false) {
    const dark = preferences.appearance === 'dark' || preferences.appearance === 'system' && colorQuery.matches;
    document.body.dataset.appearance = preferences.appearance;
    document.body.dataset.resolvedAppearance = dark ? 'dark' : 'light';
    document.body.dataset.reduceEffects = String(reduced());
    document.body.style.colorScheme = dark ? 'dark' : 'light';
    document.body.style.setProperty('--glass-alpha', String(preferences.glassOpacity));
    document.querySelectorAll('[data-appearance-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.appearanceChoice === preferences.appearance));
    });
    if (get('reduce-effects')) get('reduce-effects').checked = preferences.reduceEffects;
    if (get('glass-opacity')) get('glass-opacity').value = String(Math.round(preferences.glassOpacity * 100));
    if (get('glass-opacity-value')) get('glass-opacity-value').textContent = Math.round(preferences.glassOpacity * 100) + '%';
    if (reduced()) {
      document.body.getAnimations({subtree:true}).forEach(animation => {
        try { animation.finish(); } catch { animation.cancel(); }
      });
    }
    if (save) try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch { /* Nonessential preference. */ }
  }

  function syncButtons() {
    for (const [id, config] of Object.entries(panels)) {
      get(config.button).setAttribute('aria-expanded', String(current === id && !closing));
    }
  }

  function syncMessage() {
    get('sheet-error').textContent = fieldError || message;
    get('sheet-error').hidden = !(fieldError || message);
  }

  function restorePanel() {
    if (!current) return;
    const node = get(current);
    node.hidden = true;
    if (node.tagName === 'DETAILS') node.open = false;
    locations.get(current).replaceWith(node);
    locations.delete(current);
    current = null;
  }

  function focusBack() {
    if (trigger?.isConnected && !trigger.disabled && trigger.getClientRects().length) {
      trigger.focus({preventScroll:true});
    } else {
      const fallback = [...document.querySelectorAll('#app-content button:not(:disabled)')]
        .find(node => node.getClientRects().length);
      fallback?.focus({preventScroll:true});
    }
  }

  function close({immediate = false, restoreFocus = true} = {}) {
    if (!current) return;
    const token = ++sequence;
    const sheet = get('sheet');
    const transform = getComputedStyle(sheet).transform;
    const opacity = getComputedStyle(sheet).opacity;
    sheetAnimation?.cancel(); backdropAnimation?.cancel();
    closing = true;
    get('sheet-layer').inert = true;
    syncButtons();
    const finish = () => {
      if (token !== sequence) return;
      restorePanel();
      get('sheet-layer').hidden = true;
      get('sheet-layer').inert = false;
      get('app-content').inert = false;
      closing = false;
      fieldError = '';
      get('expected').removeAttribute('aria-invalid');
      syncButtons(); syncMessage();
      if (restoreFocus) focusBack();
    };
    if (immediate || reduced()) return finish();
    sheetAnimation = sheet.animate([{transform,opacity},{transform:'translateY(24px)',opacity:0}],
      {duration:200,easing:'cubic-bezier(.4,0,1,1)',fill:'both'});
    backdropAnimation = get('sheet-backdrop').animate([{opacity:1},{opacity:0}],{duration:180,fill:'both'});
    sheetAnimation.finished.then(finish).catch(() => {});
  }

  function open(id, {trigger:source, force = false} = {}) {
    const config = panels[id];
    if (!config || (!force && get(config.button).disabled)) return;
    const wasOpen = current === id;
    const fromTransform = wasOpen ? getComputedStyle(get('sheet')).transform : 'translateY(32px) scale(.985)';
    const fromOpacity = wasOpen ? getComputedStyle(get('sheet')).opacity : 0;
    if (current && !wasOpen) close({immediate:true,restoreFocus:false});
    ++sequence; sheetAnimation?.cancel(); backdropAnimation?.cancel();
    closing = false;
    trigger = source || get(config.button);
    if (!wasOpen) {
      const node = get(id), location = document.createComment('sheet:' + id);
      node.replaceWith(location);
      locations.set(id, location);
      get('sheet-content').append(node);
      current = id;
    }
    get(id).hidden = false;
    if (get(id).tagName === 'DETAILS') get(id).open = true;
    get('sheet-title').textContent = config.title;
    get('sheet-done').hidden = id !== 'advanced';
    fieldError = '';
    get('sheet-layer').hidden = false;
    get('sheet-layer').inert = false;
    get('app-content').inert = true;
    get('sheet-content').scrollTop = 0;
    syncButtons(); syncMessage();
    get('sheet-close').focus({preventScroll:true});
    if (!reduced()) {
      sheetAnimation = get('sheet').animate([{transform:fromTransform,opacity:fromOpacity},{transform:'translateY(0) scale(1)',opacity:1}],
        {duration:260,easing:'cubic-bezier(.2,.85,.25,1)',fill:'both'});
      backdropAnimation = get('sheet-backdrop').animate([{opacity:wasOpen ? 1 : 0},{opacity:1}],{duration:180,fill:'both'});
    }
  }

  function validateSettings() {
    if (get('expected').checkValidity()) {
      fieldError = '';
      get('expected').removeAttribute('aria-invalid');
      syncMessage();
      return true;
    }
    fieldError = '已核对总数应为 0–100000 的整数，也可以留空。';
    get('expected').setAttribute('aria-invalid','true');
    syncMessage();
    get('expected').focus({preventScroll:true});
    get('expected').reportValidity();
    return false;
  }

  function sync() {
    if (current && (get(current).hidden || get(panels[current].button).disabled ||
        get(current).tagName === 'DETAILS' && !get(current).open)) {
      close({immediate:true});
    }
    syncIcons(); syncButtons();
    if (pendingFocusRestore) { pendingFocusRestore = false; focusBack(); }
  }

  for (const [id, config] of Object.entries(panels)) {
    get(config.button).addEventListener('click', () => open(id));
    if (get(id).tagName === 'DETAILS') {
      get(id).addEventListener('toggle', () => {
        if (current === id && !get(id).open && !closing) close({immediate:true});
      });
    }
  }
  get('sheet-close').addEventListener('click', () => close());
  get('sheet-backdrop').addEventListener('click', () => close());
  get('sheet-done').addEventListener('click', () => {
    if (!validateSettings()) return;
    window.popupView.onSettingsDone?.();
    close();
  });
  get('expected').addEventListener('input', () => {
    if (fieldError && get('expected').checkValidity()) {
      fieldError = ''; get('expected').removeAttribute('aria-invalid'); syncMessage();
    }
  });
  get('sheet-layer').addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); close(); return; }
    if (event.key !== 'Tab') return;
    const focusables = [...get('sheet').querySelectorAll('button:not(:disabled),input:not(:disabled),summary,a[href]')]
      .filter(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
    const first = focusables[0], last = focusables.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  document.querySelectorAll('[data-appearance-choice]').forEach(button => button.addEventListener('click', () => {
    preferences.appearance = button.dataset.appearanceChoice;
    applyPreferences(true);
  }));
  get('reduce-effects')?.addEventListener('change', event => { preferences.reduceEffects = event.target.checked; applyPreferences(true); });
  get('glass-opacity')?.addEventListener('input', event => { preferences.glassOpacity = Math.max(.4, Math.min(1, Number(event.target.value) / 100)); applyPreferences(true); });
  motionQuery.addEventListener('change', () => applyPreferences());
  colorQuery.addEventListener('change', () => applyPreferences());
  window.popupView = {open, close, sync, reduceEffects:reduced, validateSettings,
    closeForPanel(id) {
      if (current === id) {
        close({immediate:true,restoreFocus:false});
        pendingFocusRestore = true;
      }
    },
    messageChanged(value) { message = value; syncMessage(); },
    get activePanel() { return current; }
  };
  applyPreferences(); syncIcons();
})();
