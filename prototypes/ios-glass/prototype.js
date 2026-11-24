/* Isolated design study. All data and actions are synthetic; no extension APIs. */
const $ = id => document.getElementById(id);
const panel = $('panel');
const systemMotion = matchMedia('(prefers-reduced-motion: reduce)');
let state = 'paused';
let name = '收藏 · 笔记';
let expected = 969;
let bodySaved = 840;
let recoveryReady = false;
let imagePaused = false;
let activeSheet = null;
let returnFocus = null;
let sheetSequence = 0;
let sheetAnimation = null;
let scrimAnimation = null;
let demoTimers = [];
let toastTimer;
let toastAnimation;
let renderedCounts = {list:851, body:840, total:969};
const reduced = () => systemMotion.matches || $('reduce-motion').checked;
// Inline the vendored originals so icons also render when opened via file://.
function setIcon(node, name) {
  node.innerHTML = window.PROTOTYPE_ICONS[name] || '';
  node.setAttribute('aria-hidden','true');
}
document.querySelectorAll('.icon[style]').forEach(node => {
  const name = node.style.getPropertyValue('--icon').match(/icons\/([\w-]+)\.svg/)?.[1];
  if (name) setIcon(node, name);
});
const stateNames = {ready:'待开始', paused:'已暂停', running:'保存正文中', restricted:'访问受限', unknown:'保存正文中', complete:'已结束 · 跳过 3 篇', confirm:'等待核对', disconnected:'连接中断'};

function setRing(kind, value, total) {
  const meter = $(kind + '-meter');
  const known = value !== null && Number.isFinite(total) && total > 0;
  const actual = known ? Math.min(100, value / total * 100) : null;
  const percent = known ? actual < 100 ? Math.min(99, Math.round(actual)) : 100 : null;
  meter.dataset.unknown = String(!known);
  meter.style.setProperty('--value', actual ?? 0);
  if (known) meter.setAttribute('aria-valuenow', percent); else meter.removeAttribute('aria-valuenow');
  meter.setAttribute('aria-valuetext', known ? `${value} / ${total}，${percent}%` : '总数未知');
  $(kind + '-percent').replaceChildren(document.createTextNode(known ? String(percent) : '—'));
  if (known) { const unit = document.createElement('span'); unit.textContent = '%'; $(kind + '-percent').append(unit); }
  $(kind + '-count').textContent = value ?? '—';
  $(kind + '-total').textContent = total ? ' / ' + total : value !== null ? ' 篇' : '';
}

function stopDemo() {
  demoTimers.forEach(clearTimeout); demoTimers = [];
  $('play-demo').disabled = false;
  $('play-demo').setAttribute('aria-busy', 'false');
}

function render(next, {keepDemo = false} = {}) {
  if (!keepDemo) stopDemo();
  const changed = state !== next;
  state = next;
  const isReady = state === 'ready';
  const isRunning = ['running', 'unknown'].includes(state);
  const disconnected = state === 'disconnected';
  let list = 851, body = bodySaved, total = expected;
  if (state === 'unknown') { list = 62; body = 58; total = null; }
  if (state === 'complete') { list = 969; body = 966; total = 969; }
  if (state === 'confirm') { list = 969; body = 954; total = 969; }
  if (disconnected) { list = null; body = null; total = null; }
  renderedCounts = {list, body, total};
  setRing('list', list, total);
  setRing('body', body, list);
  $('collection-name').textContent = name;
  $('task-context').textContent = isReady ? '当前收藏夹' : '收藏读取';
  $('state-label').textContent = stateNames[state];
  $('state-label').dataset.running = String(isRunning);
  if (changed && !reduced()) {
    $('state-label').getAnimations().forEach(animation => animation.cancel());
    $('state-label').animate([{opacity:.35,transform:'translateY(2px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:'ease-out'});
  }
  $('progress-card').hidden = isReady;
  $('ready-card').hidden = !isReady;
  $('ready-card').querySelector('strong').textContent = expected ? `${expected} 篇笔记` : '总数未知';
  $('list-caption').textContent = state === 'complete' ? '列表已收齐' : state === 'confirm' ? '数量齐全，待确认' : state === 'unknown' ? '总数未知' : disconnected ? '等待重新连接' : '尚未确认收齐';
  $('body-caption').textContent = state === 'complete' ? '3 篇已失效并跳过' : disconnected ? '已有正文仍保留' : '占已发现笔记';
  const notes = {
    ready:'关闭面板可继续读取，请保留收藏页。',
    paused:'已保存正文保留，随时可以继续。',
    running:'关闭面板可继续读取，请保留收藏页。',
    restricted:'读取已暂停，已保存的内容不会丢失。',
    unknown:'先收集已发现的笔记，稍后核对列表末尾。',
    complete:'已保存 966 篇正文，3 篇失效笔记已跳过。',
    confirm:'请回到收藏页，确认已经到达列表末尾。',
    disconnected:'本机连接已断开，已保存内容仍然保留。'
  };
  $('context-note').textContent = notes[state];
  $('restriction').hidden = state !== 'restricted';
  $('primary').hidden = state === 'confirm';
  $('end-actions').hidden = state !== 'confirm';
  $('primary').disabled = state === 'restricted';
  const primaryLabel = isReady ? '开始读取' : isRunning ? '暂停读取' : state === 'complete' ? '更新收藏' : disconnected ? '重新连接' : '继续读取';
  $('primary').querySelector('span').textContent = primaryLabel;
  setIcon($('primary').querySelector('.icon'), isRunning ? 'pause' : disconnected ? 'rotate-cw' : 'play');
  $('primary').title = state === 'restricted' ? '完成网页验证后，先点击右上角重新检查' : '';
  $('refresh').setAttribute('aria-label', state === 'restricted' ? '重新检查访问限制' : '刷新进度');
  const connected = document.querySelector('.connected');
  connected.lastChild.textContent = disconnected ? '未连接' : '已连接';
  connected.style.color = disconnected ? 'var(--muted)' : '';
  $('settings-button').disabled = !isReady;
  $('settings-button').title = isReady ? '修改名称与核对总数' : '任务进行中，请在开始前设置名称与总数';
  $('details-button').disabled = isReady;
  $('recover').disabled = !recoveryReady || isRunning || state === 'restricted' || disconnected;
  $('recover').title = $('recover').disabled ? '当前任务无需单独补全；访问受限时需先重新检查' : '通过浏览器补全未完成正文';
  const currentImages = document.querySelector('[data-action="current-image"]');
  currentImages.disabled = true;
  currentImages.title = '打开笔记详情页后可获取当前笔记图片';
  document.querySelector('[data-action="collection-images"]').disabled = isReady || disconnected || state === 'restricted';
  document.querySelectorAll('[data-state]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.state === state)));
}

function toast(message) {
  clearTimeout(toastTimer); toastAnimation?.cancel();
  $('toast').textContent = message; $('toast').hidden = false;
  if (!reduced()) toastAnimation = $('toast').animate([{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:'ease-out'});
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2600);
}

const detailsMarkup = () => `
  <p class="sheet-caption">列表与正文分别统计。正文比例只对应已发现的笔记。</p>
  <div class="group"><div class="data-row"><span>发现笔记</span><strong>${renderedCounts.list ?? '—'} 篇</strong></div><div class="data-row"><span>已保存正文</span><strong>${renderedCounts.body ?? '—'} 篇</strong></div><div class="data-row"><span>列表核对</span><strong>${state === 'complete' ? '已确认收齐' : '尚未确认'}</strong></div><div class="data-row"><span>任务状态</span><strong>${stateNames[state]}</strong></div></div>
  <details class="collapse collapse-arrow"><summary class="collapse-title">${state === 'complete' ? '失效列表 · 3 篇' : '进度含义'}</summary><div class="collapse-content">${state === 'complete' ? '<p>示例笔记 01 · 笔记已不可访问</p><p>示例笔记 02 · 访问链接失效</p><p>示例笔记 03 · 笔记已不可访问</p>' : '<p>收藏列表：已发现数量 / 已知总数。数量达到 100% 后，仍需确认列表末尾。</p><p>正文保存：已保存正文 / 已发现数量，不代表整个收藏已归档。</p>'}</div></details>`;

function settingsMarkup() {
  return `<p class="sheet-caption">开始前核对一次，之后专心阅读。</p><form id="settings-form"><label class="form-label" for="demo-name">本地名称 <span>默认跟随页面</span></label><input class="input" id="demo-name" maxlength="200" autocomplete="off"><label class="form-label" for="demo-total">已核对总数 <span>可留空</span></label><input class="input" id="demo-total" type="number" min="0" max="100000" step="1" placeholder="总数未知则留空"><p class="form-hint">仅在识别有误时修正。总数留空时，列表进度显示为“—”。</p><button type="submit" class="btn primary">完成</button></form>`;
}

function imagesMarkup() {
  return `<p class="sheet-caption">图片单独保存，不计入正文进度。</p><div class="group"><div class="image-summary"><strong>24</strong><p>张图片已保存</p></div><div class="data-row"><span>图片齐全的笔记</span><strong>3 / 12 篇</strong></div><div class="data-row"><span>图片任务</span><strong id="image-state">${imagePaused ? '已暂停' : '补图中'}</strong></div></div><button id="image-toggle" class="btn primary">${imagePaused ? '继续补图' : '暂停补图'}</button><p class="form-hint">此操作只影响图片任务，正文读取状态不变。</p>`;
}

function populateSheet(kind) {
  const titles = {details:'读取详情',settings:'名称与总数',help:'使用帮助',images:'图片任务',recover:'补全正文'};
  $('sheet-title').textContent = titles[kind];
  const templates = {
    details:detailsMarkup,
    settings:settingsMarkup,
    images:imagesMarkup,
    help:() => `<div class="sheet-copy"><p><strong>先读取，再搜索。</strong><br>收藏列表和笔记正文保存在本机，供 Agent 搜索和引用。</p><p><strong>关闭面板，任务继续。</strong><br>读取列表时，请保留收藏页；暂停后随时可以接着读。</p><p><strong>正文与图片分开保存。</strong><br>图片按需补充，暂不处理 OCR 和视频。补全正文时会打开后台笔记页。</p></div><button id="help-done" class="btn primary">知道了</button>`,
    recover:() => `<p class="sheet-caption">将通过浏览器逐篇读取未完成正文，遇到登录或验证会暂停。</p><div class="group"><div class="data-row"><span>待补全正文</span><strong>11 篇</strong></div><div class="data-row"><span>已保存正文</span><strong>840 篇</strong></div></div><button id="begin-recovery" class="btn primary">开始补全</button>`
  };
  $('sheet-body').innerHTML = templates[kind]();
  if (kind === 'settings') {
    $('demo-name').value = name;
    $('demo-total').value = expected ?? '';
    $('settings-form').addEventListener('submit', event => {
      event.preventDefault();
      if (!$('settings-form').reportValidity()) return;
      name = $('demo-name').value.trim() || '收藏 · 笔记';
      expected = $('demo-total').value.trim() ? Number($('demo-total').value) : null;
      render('ready'); closeSheet(); toast('演示：设置已更新，下次开始时生效');
    });
  }
  if (kind === 'images') $('image-toggle').addEventListener('click', () => {
    imagePaused = !imagePaused;
    $('image-state').textContent = imagePaused ? '已暂停' : '补图中';
    $('image-toggle').textContent = imagePaused ? '继续补图' : '暂停补图';
  });
  if (kind === 'help') $('help-done').addEventListener('click', closeSheet);
  if (kind === 'recover') $('begin-recovery').addEventListener('click', () => { closeSheet(); render('running'); toast('演示：开始补全正文'); });
}

function openSheet(kind, trigger) {
  if (kind === 'settings' && state !== 'ready') return;
  clearTimeout(toastTimer); $('toast').hidden = true;
  const wasOpen = !$('sheet-layer').hidden;
  const currentTransform = wasOpen ? getComputedStyle($('sheet')).transform : 'translateY(32px) scale(.985)';
  const currentOpacity = wasOpen ? getComputedStyle($('sheet')).opacity : '0';
  ++sheetSequence; sheetAnimation?.cancel(); scrimAnimation?.cancel();
  if (!wasOpen) returnFocus = trigger || document.activeElement;
  activeSheet = kind;
  populateSheet(kind);
  $('sheet-layer').hidden = false;
  $('sheet-layer').inert = false;
  $('app-content').inert = true;
  document.querySelectorAll('[data-sheet]').forEach(button => button.setAttribute('aria-expanded', String(button.dataset.sheet === kind)));
  $('close-sheet').focus({preventScroll:true});
  $('sheet-body').scrollTop = 0;
  if (!reduced()) {
    sheetAnimation = $('sheet').animate([{transform:currentTransform,opacity:currentOpacity},{transform:'translateY(0) scale(1)',opacity:1}],{duration:260,easing:'cubic-bezier(.2,.85,.25,1)',fill:'both'});
    scrimAnimation = $('scrim').animate([{opacity:wasOpen ? 1 : 0},{opacity:1}],{duration:180,easing:'ease-out',fill:'both'});
  }
}

function closeSheet() {
  if ($('sheet-layer').hidden) return;
  const sequence = ++sheetSequence;
  const transform = getComputedStyle($('sheet')).transform;
  const opacity = getComputedStyle($('sheet')).opacity;
  sheetAnimation?.cancel(); scrimAnimation?.cancel();
  $('sheet-layer').inert = true;
  const finish = () => {
    if (sequence !== sheetSequence) return;
    $('sheet-layer').hidden = true;
    $('sheet-layer').inert = false;
    $('app-content').inert = false;
    activeSheet = null;
    document.querySelectorAll('[data-sheet]').forEach(button => button.setAttribute('aria-expanded','false'));
    if (returnFocus?.isConnected && !returnFocus.disabled) returnFocus.focus({preventScroll:true}); else $('refresh').focus({preventScroll:true});
  };
  if (reduced()) return finish();
  sheetAnimation = $('sheet').animate([{transform,opacity},{transform:'translateY(24px)',opacity:0}],{duration:200,easing:'cubic-bezier(.4,0,1,1)',fill:'both'});
  scrimAnimation = $('scrim').animate([{opacity:1},{opacity:0}],{duration:180,easing:'ease-out',fill:'both'});
  sheetAnimation.finished.then(finish).catch(() => {});
}

$('sheet-layer').addEventListener('keydown', event => {
  if (event.key === 'Escape') { event.preventDefault(); closeSheet(); }
  if (event.key !== 'Tab') return;
  const focusables = [...$('sheet').querySelectorAll('button:not(:disabled), input:not(:disabled), summary, a[href]')].filter(node => node.checkVisibility());
  const first = focusables[0], last = focusables.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
$('close-sheet').addEventListener('click', closeSheet);
$('scrim').addEventListener('click', closeSheet);
document.querySelectorAll('[data-sheet]').forEach(button => button.addEventListener('click', () => openSheet(button.dataset.sheet, button)));
document.querySelectorAll('[data-state]').forEach(button => button.addEventListener('click', () => {
  closeSheet(); render(button.dataset.state); $('scroll-area').scrollTop=0;
}));
$('primary').addEventListener('click', () => {
  if (state === 'disconnected') { render('paused'); toast('演示：已重新连接，请手动继续'); }
  else render(['running','unknown'].includes(state) ? 'paused' : 'running');
});
$('refresh').addEventListener('click', () => {
  if (!reduced()) $('refresh').querySelector('.icon').animate([{transform:'rotate(0deg)'},{transform:'rotate(360deg)'}],{duration:320,easing:'ease-out'});
  if (state === 'restricted') { recoveryReady = true; render('paused'); toast('演示：限制已解除，请手动继续'); }
  else if (state === 'disconnected') { render('paused'); toast('演示：本机连接已恢复'); }
  else toast('演示：进度已刷新');
});
$('confirm-end').addEventListener('click', () => { render('complete'); toast('演示：已确认列表末尾，3 篇失效笔记已跳过'); });
$('save-partial').addEventListener('click', () => { render('paused'); toast('演示：已保存读到的内容，尚未确认收齐'); });
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => {
  if (button.dataset.action === 'source') toast('演示：返回收藏页；本稿不会打开真实页面');
  else if (button.dataset.action === 'recover') openSheet('recover',button);
  else openSheet('images',button);
}));
$('preview-settings').addEventListener('click', () => { render('ready'); openSheet('settings',$('settings-button')); });

function setAppearance(appearance) {
  panel.dataset.appearance = appearance;
  $('light-mode').setAttribute('aria-pressed',String(appearance === 'light'));
  $('dark-mode').setAttribute('aria-pressed',String(appearance === 'dark'));
}
$('light-mode').addEventListener('click', () => setAppearance('light'));
$('dark-mode').addEventListener('click', () => setAppearance('dark'));
$('opacity-control').addEventListener('input', event => {
  const value = Number(event.target.value) / 100;
  panel.style.setProperty('--glass-alpha', value);
  panel.style.setProperty('--user-glass-alpha', value);
  $('opacity-value').textContent = `${event.target.value}%`;
});
function applyMotion() {
  panel.dataset.reduce = String(reduced());
  if (reduced()) {
    panel.getAnimations({subtree:true}).forEach(animation => { try { animation.finish(); } catch { animation.cancel(); } });
  }
}
$('reduce-motion').checked = systemMotion.matches;
$('reduce-motion').addEventListener('change', applyMotion);
systemMotion.addEventListener('change', applyMotion);
$('play-demo').addEventListener('click', () => {
  closeSheet(); bodySaved=840; render('running');
  $('play-demo').disabled=true; $('play-demo').setAttribute('aria-busy','true');
  for (let i=1;i<=4;i++) demoTimers.push(setTimeout(() => { bodySaved=840+i*2; render('running',{keepDemo:true}); },i*650));
  demoTimers.push(setTimeout(() => { render('paused'); toast('演示完成，已暂停'); },3200));
});
render('paused'); applyMotion();
// Deliberately exposed only for local design validation.
window.designStudy = {render, openSheet, closeSheet, setAppearance, getState:() => state};
