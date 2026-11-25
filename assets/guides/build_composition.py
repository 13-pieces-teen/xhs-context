"""Build the deterministic HyperFrames tutorial; all examples are synthetic."""
from pathlib import Path
import json
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROJECT = HERE / 'hyperframes'
SHARED = PROJECT / 'shared'
SHARED.mkdir(parents=True, exist_ok=True)
for name in ('MiSans-Regular.woff2', 'MiSans-Semibold.woff2', 'MiSans-license.pdf'):
    shutil.copyfile(ROOT / 'browser-extension/fonts' / name, SHARED / name)
shutil.copyfile(ROOT / 'assets/brand/xhs-context-mark-256.png', SHARED / 'mark.png')
for name in ('ready', 'running', 'paused', 'completed'):
    shutil.copyfile(HERE / 'panels' / (name + '.png'), SHARED / (name + '.png'))
gsap = ROOT / '.tmp/hyperframes-tools/node_modules/gsap'
shutil.copyfile(gsap / 'dist/gsap.min.js', SHARED / 'gsap.min.js')
shutil.copyfile(gsap / 'README.md', SHARED / 'GSAP-README.md')

def window(title, body, cls=''):
    return f'<div class="window {cls}"><div class="window-bar"><span class="dots">● ● ●</span><span>{title}</span><span class="dim">操作示意</span></div><div class="window-body">{body}</div></div>'

def note(title, body, hint):
    return f'<aside class="instruction"><div class="step-word">跟着做</div><h2>{title}</h2><p>{body}</p><div class="tip">{hint}</div></aside>'

def pointer(x, y):
    return f'<div class="pointer" style="left:{x}px;top:{y}px" data-layout-ignore><svg width="38" height="48" viewBox="0 0 38 48"><path d="M3 2 L31 29 L19 30 L26 43 L18 47 L11 33 L3 41 Z" fill="#242529" stroke="#FFFFFF" stroke-width="2"/></svg></div><div class="ring" style="left:{x-23}px;top:{y-23}px" data-layout-ignore></div>'

scenes = []
def scene(group, title, subtitle, content, x, y):
    scenes.append(dict(group=group, title=title, subtitle=subtitle, content=content, x=x, y=y))

scene('01 · 安装项目', '复制安装提示词', '在 README「开始使用」中找到这段话。',
      window('README · 开始使用', '<div class="small-label">让 Agent 辅助安装</div><div class="code-card"><p>请帮我安装 xhs-context</p><p class="url">github.com/13-pieces-teen/xhs-context</p><p>按安装指南检查环境，保留已有资料库。</p><p>告诉我下一步需要操作什么。</p><div class="button target">复制整段提示词</div></div>') + note('先复制', '复制 README 中的完整安装提示词。', '这里只展示摘要，请复制 README 的原段落。'), 585, 552)
scene('01 · 安装项目', '粘贴给 Agent', '打开能执行本机命令的 Agent，例如 Codex。',
      window('Agent 对话', '<div class="chat-space"><div class="small-label">新对话</div><div class="chat-input"><p>请帮我安装 xhs-context：</p><p class="url">https://github.com/13-pieces-teen/xhs-context</p><p>优先复用本机安装，按项目指南完成配置。</p><div class="button target">发送 ↑</div></div></div>') + note('粘贴并发送', '让 Agent 检查环境并执行安装。', '需要 Windows；缺少依赖时按 Agent 的说明处理。'), 601, 553)
scene('01 · 安装项目', '记下扩展文件夹', '等 Agent 检查完成，再进入下一段「加载扩展」。',
      window('安装结果 · 示例', '<div class="result"><p class="result-title">本地组件检查通过</p><p>下一步：在浏览器加载这个文件夹</p><div class="folder-path">D:\\Tools\\xhs-context\\<strong>browser-extension</strong></div><p class="muted">这是示例路径，请使用你的安装结果。</p></div>') + note('保留目录', '记下 Agent 返回的 browser-extension 路径。', '本地安装完成后，仍需加载浏览器扩展。'), 380, 484)

scene('02 · 加载扩展', '打开扩展管理页', 'Chrome 输入 chrome://extensions 后按回车。',
      window('Chrome', '<div class="address target">chrome://extensions</div><div class="manager-title"><h3>扩展程序</h3><div class="dev"><span>开发者模式</span><span class="toggle"></span></div></div><div class="empty">先打开右上角「开发者模式」</div>') + note('打开开关', '进入扩展管理页，开启「开发者模式」。', 'Edge 用户输入 edge://extensions。'), 663, 377)
scene('02 · 加载扩展', '加载已解压的扩展', '启用开发者模式后，点击页面左上方的按钮。',
      window('Chrome · 扩展程序', '<div class="address">chrome://extensions</div><div class="manager-title"><h3>扩展程序</h3><div class="dev"><span>开发者模式</span><span class="toggle on"></span></div></div><div class="button outline target">加载已解压的扩展程序</div><div class="empty small">接下来会打开文件夹选择窗口</div>') + note('点击加载', '点击「加载已解压的扩展程序」。', '选择文件夹，不是 ZIP 压缩包。'), 300, 426)
scene('02 · 加载扩展', '选中正确的文件夹', '使用上一步安装时返回的位置。',
      window('选择扩展程序目录', '<div class="address">D:\\Tools\\xhs-context</div><div class="file-row">▰　docs</div><div class="file-row selected target">▰　browser-extension</div><div class="file-row">▰　scripts</div><div class="file-footer"><span>选中 browser-extension</span><div class="button">选择文件夹</div></div>') + note('确认这个目录', '选中 browser-extension，再点「选择文件夹」。', '加载成功后，在浏览器的扩展菜单找到「收藏上下文」。'), 607, 573)

scene('03 · 保存收藏', '打开自己的收藏页', '在已登录的小红书个人主页，进入「收藏 → 笔记」。',
      window('小红书个人主页 · 简化示意', '<div class="profile">你的个人主页</div><div class="tabs"><span>笔记</span><strong class="target">收藏</strong></div><div class="subtabs">笔记</div><div class="cards"><div>学习资料</div><div>旅行攻略</div><div>生活灵感</div></div><p class="muted">打开浏览器扩展菜单 → 收藏上下文</p>') + note('先选范围', '确认是你想读取的收藏列表，再打开扩展。', '不要在搜索页或别人的主页直接开始。'), 250, 374)
scene('03 · 保存收藏', '点击「开始读取」', '核对当前收藏夹，并确认面板显示「已连接」。',
      '<div class="panel-wrap" data-layout-allow-overflow><img class="panel" src="shared/ready.png" alt="当前扩展准备读取，合成数据"><div class="panel-highlight" style="top:293px;left:28px;width:448px;height:64px" data-layout-ignore></div></div>' + note('开始保存正文', '点击面板里的「开始读取」。', '读取列表期间，请保留原来的收藏标签页。'), 377, 413)
scene('03 · 保存收藏', '分别查看两种进度', '收藏列表收集与正文保存是两个不同的进度。',
      '<div class="panel-wrap" data-layout-allow-overflow><img class="panel" src="shared/running.png" alt="当前扩展读取中，合成数据"></div>' + note('需要停下时暂停', '点击「暂停读取」可以暂停；已保存的正文保留。', '正文保存不代表图片齐全。遇到访问限制时停止采集。'), 368, 514)

scene('04 · 开始提问', '开启新的 Codex 聊天', '安装 Skill 后，在新聊天里开始使用。',
      window('Agent 客户端 · 简化示意', '<div class="chat-nav"><div class="button outline target">＋ 新聊天</div></div><div class="empty">准备向你的收藏提问</div>') + note('新建聊天', '开启新聊天，明确要求使用 xhs-context。', '没有自动识别时，提供安装返回的 Skill 路径。'), 240, 317)
scene('04 · 开始提问', '告诉它你想找什么', '先说使用 xhs-context，再写具体问题。',
      window('Agent 对话 · 示例', '<div class="chat-input"><p>使用 xhs-context 查询我的收藏。</p><p>找出马德里的小吃，列出店名、</p><p>作者提到的特点和原文出处。</p><p>不确定或缺失的内容请标出来。</p><div class="button target">发送 ↑</div></div>') + note('描述真实问题', '可以问旅行、学习资料，也可以指定某篇图文笔记。', '查询已有资料，不会自动采集或补图。'), 604, 553)
scene('04 · 开始提问', '查看出处，再继续追问', '有依据的回答，也应告诉你哪些内容没有读到。',
      window('回答结构 · 示例，不是实际收藏结果', '<div class="answer"><h3>根据已保存的原文整理</h3><p>店名 / 方法 / 推荐依据</p><div class="source-chip target">原文链接 · 保存版本</div><p class="gap">缺失说明：有图片尚未保存</p><div class="followup">继续追问：对比这两篇笔记的建议。</div></div>') + note('核对来源', '查看原文出处，留意缺失说明，再按需要追问。', '结果取决于实际保存的资料；没有证据时应明确说明。'), 306, 426)

css = r'''
@font-face{font-family:MiSans;src:url('shared/MiSans-Regular.woff2')}@font-face{font-family:MiSans;src:url('shared/MiSans-Semibold.woff2');font-weight:600}
*{box-sizing:border-box}body{margin:0;width:1280px;height:800px;overflow:hidden;background:#F5F5F7;color:#242529;font-family:MiSans,sans-serif}p,h1,h2,h3{margin:0}#root{width:1280px;height:800px;position:relative}.scene{position:absolute;inset:0;background:#F5F5F7}.scene-content{width:100%;height:100%;padding:28px 48px 24px;display:flex;flex-direction:column;gap:14px}.brand{display:flex;align-items:center;justify-content:space-between;height:30px;font-size:21px;color:#55565D}.brand-name{display:flex;align-items:center;gap:10px}.brand img{width:30px;height:30px}.badge{font-size:21px;color:#A64733}.title{font-size:60px;line-height:1.2;font-weight:600;letter-spacing:-1px}.subtitle{font-size:26px;color:#55565D;line-height:1.4}.demo{display:flex;align-items:center;gap:40px;flex:1;min-height:0;position:relative}.window{height:440px;width:710px;flex-shrink:0;background:#FFFFFF;border:1px solid #D8D8DC;border-radius:22px;overflow:hidden;box-shadow:0 10px 28px #2425290b}.window-bar{height:48px;padding:0 20px;background:#ECECEF;display:flex;align-items:center;justify-content:space-between;font-size:21px;gap:16px}.dots{font-size:13px;color:#A64733;letter-spacing:3px}.dim{font-size:21px;color:#55565D}.window-body{padding:26px 30px;font-size:24px;line-height:1.5}.small-label{font-size:22px;font-weight:600;margin-bottom:12px}.code-card{background:#FCF7F5;padding:20px;border:1px solid #E1C8C1;border-radius:12px;font-size:23px;line-height:1.65}.code-card .url{font-size:21px}.button{font-size:22px;line-height:1.3;color:#FFFFFF;background:#A64733;border-radius:12px;padding:13px 22px;display:inline-block;margin-top:14px;font-weight:600}.outline{background:#FCF7F5;border:1px solid #A64733;color:#A64733}.code-card .button,.chat-input .button{display:block;width:fit-content;margin-left:auto}.instruction{display:flex;flex-direction:column;gap:18px;flex:1;min-width:0}.step-word{color:#A64733;font-size:22px;font-weight:600}.instruction h2{font-size:36px;line-height:1.3}.instruction p{font-size:28px;line-height:1.6}.tip{font-size:23px;line-height:1.55;padding:18px;background:#FCF7F5;border:1px solid #E1C8C1;border-radius:16px;color:#55565D}.footer{height:46px;display:flex;align-items:center;justify-content:space-between;border-top:1px solid #D8D8DC;padding-top:14px;font-size:21px;color:#55565D}.step-dots{display:flex;gap:8px;align-items:center}.step-dots span{height:5px;width:54px;background:#D8D8DC;border-radius:4px}.step-dots .active{background:#A64733}.pointer{position:absolute;z-index:80;filter:drop-shadow(0 3px 2px #24252925)}.ring{position:absolute;width:46px;height:46px;border:4px solid #A64733;border-radius:50%;z-index:79}.chat-input{padding:24px;background:#F5F5F7;border:1px solid #D8D8DC;border-radius:18px;font-size:24px;line-height:1.6}.chat-input .url{font-size:21px}.result{padding:12px;line-height:1.6}.result-title{font-size:32px;font-weight:600;color:#A64733;margin-bottom:16px}.folder-path{padding:18px;background:#FCF7F5;border:2px solid #A64733;border-radius:12px;margin:20px 0;font-size:21px;overflow-wrap:anywhere}.muted{color:#55565D;font-size:21px;line-height:1.5}.address{background:#F5F5F7;border:1px solid #D8D8DC;padding:9px 20px;border-radius:20px;font-size:24px;margin-bottom:24px}.manager-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}.manager-title h3{font-size:28px}.dev{display:flex;align-items:center;gap:10px;font-size:21px}.toggle{width:42px;height:24px;border-radius:15px;background:#88898F;display:inline-block;position:relative}.toggle:after{content:'';background:#FFFFFF;width:18px;height:18px;border-radius:50%;position:absolute;left:3px;top:3px}.toggle.on{background:#A64733}.toggle.on:after{left:21px}.empty{padding:35px 10px;color:#55565D;text-align:center;font-size:24px}.empty.small{padding:24px 0;font-size:22px}.file-row{font-size:24px;padding:10px 16px;border-radius:8px}.selected{background:#FCF7F5;outline:2px solid #A64733;color:#A64733}.file-footer{display:flex;align-items:center;justify-content:space-between;font-size:21px;margin-top:8px}.file-footer .button{margin:0}.profile{font-size:28px;font-weight:600;margin-bottom:20px}.tabs{display:flex;gap:40px;border-bottom:1px solid #D8D8DC;padding-bottom:12px}.tabs strong{color:#A64733;border-bottom:3px solid #A64733}.subtabs{font-size:22px;margin:12px 0}.cards{display:flex;gap:16px;margin-bottom:22px}.cards div{background:#FCF7F5;border:1px solid #E1C8C1;flex:1;height:90px;border-radius:12px;display:grid;place-items:center;font-size:22px}.panel-wrap{position:relative;width:504px;height:480px;margin-left:40px;margin-right:30px;flex-shrink:0;box-shadow:0 12px 36px #24252915;border-radius:20px;overflow:hidden}.panel{width:504px;height:840px;display:block}#scene-8 .panel{transform:translateY(-70px)}.progress-zoom p{font-size:27px;line-height:1.6}.panel-highlight{position:absolute;border:3px solid #A64733;border-radius:20px;box-shadow:0 0 0 4px #FFFFFF80}.panel-wrap + .instruction{padding-right:40px}.answer{padding:8px;line-height:1.65}.answer h3{font-size:28px;margin-bottom:20px}.source-chip{display:inline-block;padding:8px 16px;background:#FCF7F5;border:1px solid #A64733;color:#A64733;border-radius:12px;margin:14px 0;font-size:23px}.gap{color:#55565D;font-size:23px}.followup{padding:16px;background:#F5F5F7;border-radius:12px;margin-top:20px;font-size:23px}
'''
html_scenes=[]
for i,s in enumerate(scenes):
    n=i%3+1
    dots=''.join(f'<span class="{"active" if j < n else ""}"></span>' for j in range(3))
    content=s['content']
    if i==8:
        content=content.replace('<p>点击「暂停读取」可以暂停；已保存的正文保留。</p>', '<div class="progress-zoom"><p>收藏列表：36 / 60 篇</p><p>正文保存：24 / 36 篇</p><p>点「暂停读取」可暂停。</p></div>')
    if i==3:
        content=content.replace('address target', 'address').replace('class="toggle"', 'class="toggle target"')
    html_scenes.append(f'''<section class="scene clip" id="scene-{i}" data-start="{i*8}" data-duration="{8.35 if i<11 else 8}" data-track-index="{i}" data-hold="Intentional reading pause in a beginner GIF tutorial" style="z-index:{i}"><div class="scene-content"><div class="brand"><div class="brand-name"><img src="shared/mark.png" alt="">收藏上下文 · 新手指南</div><span class="badge">{s['group']}</span></div><h1 class="title">{s['title']}</h1><p class="subtitle">{s['subtitle']}</p><div class="demo">{content}</div><footer class="footer"><span>操作示意 · 示例数据 · 不代表真实安装或采集结果</span><div class="step-dots">{dots}<b> {n} / 3</b></div></footer></div>{pointer(s['x'], s['y'])}</section>''')
script='''window.__timelines=window.__timelines||{};const tl=gsap.timeline({paused:true});
for(let i=0;i<12;i++){const s='#scene-'+i;const t=i*8;
const target=document.querySelector(s+' .target');
const panel=document.querySelector(s+' .panel');
let pos;
if(target){const b=target.getBoundingClientRect();pos={x:b.x+b.width*.65,y:b.y+b.height*.55};}
if(panel){const b=panel.getBoundingClientRect();pos={x:b.x+175*b.width/360,y:b.y+(i===7?232.1875:358.390625)*b.width/360};}
if(pos){const p=document.querySelector(s+' .pointer'),r=document.querySelector(s+' .ring');p.style.left=pos.x+'px';p.style.top=pos.y+'px';r.style.left=(pos.x-23)+'px';r.style.top=(pos.y-23)+'px';}
tl.from(s,{opacity:0,duration:.35,ease:'sine.inOut',immediateRender:true},t);
tl.from(s+' .title',{y:12,opacity:0,duration:.4,ease:'power3.out',immediateRender:true},t+.15);
tl.from(s+' .subtitle',{x:8,opacity:0,duration:.35,ease:'power2.out',immediateRender:true},t+.25);
tl.from(s+' .demo',{y:8,opacity:0,duration:.4,ease:'sine.out',immediateRender:true},t+.35);
tl.from(s+' .pointer',{x:90,y:65,opacity:0,duration:.65,ease:'power2.inOut',immediateRender:true},t+1.5);
tl.from(s+' .ring',{scale:.5,opacity:0,duration:.4,ease:'power2.out',immediateRender:true},t+2.15);
tl.to(s+' .ring',{scale:1.2,duration:.3,yoyo:true,repeat:1,ease:'sine.inOut'},t+2.6);
}window.__timelines['main']=tl;'''
html='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=1280,height=800"><title>收藏上下文 · 四步上手</title><script src="shared/gsap.min.js"></script><style>'+css+'</style></head><body><div id="root" data-composition-id="main" data-start="0" data-duration="96" data-width="1280" data-height="800">'+''.join(html_scenes)+'</div><script>'+script+'</script></body></html>'
(PROJECT/'index.html').write_text(html,encoding='utf-8')
segments=[{'name':name,'start':i*24,'duration':24,'hero_times':[i*24+4,i*24+12,i*24+20]} for i,name in enumerate(['01-install','02-extension','03-save','04-ask'])]
(HERE/'segments.json').write_text(json.dumps(segments,indent=2),encoding='utf-8')
shutil.copyfile(HERE/'DESIGN.md',PROJECT/'DESIGN.md')
print('Built 12 scenes, four 24-second guides, 96 seconds total.')
