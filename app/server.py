"""FastAPI 入口：提供 /api/chat、静态文件、网页 UI。"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import litellm
from pydantic import BaseModel

from . import accounting, agent, memory, relay, tools
from .config import settings

app = FastAPI(title="Auctus Agent")
app.include_router(relay.router)

# 输出目录公开下载（仅本地服务，不暴露公网）
app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")


WEB_UI = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8") if (Path(__file__).parent / "ui.html").exists() else """
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auctus Agent</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f7f7f4;color:#222}
header{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid #ddd;background:#fff}
main{height:calc(100vh - 53px);min-height:0}
.chat{height:100%;display:flex;flex-direction:column;min-width:0;min-height:0}
#messages{flex:1;min-height:0;overflow:auto;padding:18px;white-space:pre-wrap;scroll-behavior:smooth}
.msg{max-width:840px;margin:0 0 14px;padding:10px 12px;border:1px solid #ddd;background:#fff;border-radius:6px}
.user{background:#eef6ff;border-color:#cde4ff;margin-left:auto}
.agent{background:#fff}
.pending{color:#555;background:#fbfbfb}
.pending-main{display:block}
.pending-detail{display:block;margin-top:6px;color:#8a8a8a;font-size:12px;line-height:1.35}
.typing{display:inline-flex;gap:4px;margin-left:6px;vertical-align:middle}
.typing span{width:5px;height:5px;border-radius:50%;background:#777;display:inline-block;animation:typingPulse 1s infinite ease-in-out}
.typing span:nth-child(2){animation-delay:.15s}
.typing span:nth-child(3){animation-delay:.3s}
@keyframes typingPulse{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
form{display:flex;gap:8px;padding:12px 18px;border-top:1px solid #ddd;background:#fff}
input,select,button{font:inherit}
#message{flex:1;padding:10px;border:1px solid #bbb;border-radius:6px}
button{padding:9px 12px;border:1px solid #999;border-radius:6px;background:#fff;cursor:pointer}
button.primary{background:#1f6feb;color:white;border-color:#1f6feb}
button.icon{width:38px;height:38px;padding:0;display:inline-flex;align-items:center;justify-content:center;font-size:20px;line-height:1;border-radius:6px}
aside{position:fixed;top:0;right:0;width:min(420px,92vw);height:100vh;box-sizing:border-box;border-left:1px solid #ddd;background:#fff;overflow:auto;padding:14px;z-index:20;transform:translateX(100%);transition:transform .18s ease;box-shadow:-18px 0 50px rgba(0,0,0,.14)}
aside.open{transform:translateX(0)}
.settings-backdrop{position:fixed;inset:0;background:rgba(20,24,31,.28);display:none;z-index:19}
.settings-backdrop.open{display:block}
.settings-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #e5e5e5}
.settings-head h1{font-size:18px;line-height:1.2;margin:0;letter-spacing:0}
section{margin-bottom:18px}
h2{font-size:14px;margin:0 0 8px;color:#555;text-transform:uppercase;letter-spacing:.04em}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.row>*{min-width:0}
.stack{display:grid;gap:8px}
.item{border:1px solid #ddd;border-radius:6px;padding:8px;background:#fafafa}
.muted{color:#666;font-size:12px}
.file{display:block;margin-top:6px}
.overlay{position:fixed;inset:0;background:rgba(20,24,31,.38);display:none;align-items:center;justify-content:center;padding:18px;z-index:10}
.folder-overlay{position:fixed;inset:0;background:rgba(20,24,31,.38);display:none;align-items:center;justify-content:center;padding:18px;z-index:30}
.folder-dialog{width:min(720px,96vw);max-height:86vh;display:flex;flex-direction:column;background:#fff;border:1px solid #d4d4d4;border-radius:8px;box-shadow:0 24px 80px rgba(0,0,0,.22)}
.folder-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px;border-bottom:1px solid #e3e3e3}
.folder-head h1{font-size:18px;line-height:1.2;margin:0;letter-spacing:0}
.folder-path{padding:10px 14px;border-bottom:1px solid #eee;font-size:12px;color:#666;word-break:break-all}
.folder-list{overflow:auto;padding:8px;display:grid;gap:6px}
.folder-row{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;text-align:left;border:1px solid #e1e1e1;border-radius:6px;background:#fff;padding:9px 10px}
.folder-row:hover{background:#f6f8fa}
.folder-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 14px;border-top:1px solid #e3e3e3}
.wizard{width:min(760px,100%);max-height:92vh;overflow:auto;background:#fff;border:1px solid #d4d4d4;border-radius:8px;box-shadow:0 24px 80px rgba(0,0,0,.22)}
.wizard header{height:auto;display:block;padding:18px;border-bottom:1px solid #e3e3e3}
.wizard h1{font-size:22px;line-height:1.2;margin:0 0 6px;letter-spacing:0}
.wizard form{display:block;padding:18px;border:0}
.choice-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0 16px}
.choice{border:1px solid #ccc;border-radius:8px;padding:12px;background:#fafafa;cursor:pointer}
.choice input{margin-right:6px}
.choice strong{display:block;margin-bottom:4px}
.choice span{display:block;font-size:12px;color:#666}
.field{display:grid;gap:6px;margin-bottom:12px}
.field label{font-size:13px;color:#555;font-weight:600}
.field input,.field textarea,.field select{padding:9px;border:1px solid #bbb;border-radius:6px}
.field textarea{min-height:76px;resize:vertical}
.wizard-panel{display:none;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:12px;background:#fbfbfb}
.wizard-panel.active{display:block}
.wizard-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:14px}
.error{color:#9d1c1c;font-size:13px}
@media(max-width:860px){main{height:calc(100vh - 53px)}.chat{height:100%}}
@media(max-width:720px){.choice-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <strong>Auctus Agent</strong>
  <button id="openSettings" class="icon" type="button" title="设置" aria-label="设置">⚙</button>
</header>
<main>
  <div class="chat">
    <div id="messages"></div>
    <form id="chatForm">
      <input id="message" autocomplete="off" placeholder="输入任务，例如：读取 test_prd.md 并生成总结">
      <button class="primary">发送</button>
    </form>
  </div>
</main>
<div id="settingsBackdrop" class="settings-backdrop"></div>
<aside id="settingsPanel" aria-label="设置">
    <div class="settings-head">
      <h1>设置</h1>
      <button id="closeSettings" class="icon" type="button" title="关闭" aria-label="关闭">×</button>
    </div>
    <section>
      <h2>模型</h2>
      <div class="row">
        <select id="model"></select>
        <button id="saveModel">切换</button>
        <button id="openSetup" type="button">重新设置</button>
      </div>
      <div class="row">
        <select id="systemLanguage">
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
        <button id="saveLanguage" type="button">保存语言</button>
      </div>
      <div class="row">
        <select id="hostedRegion">
          <option value="auto">自动选择区域</option>
          <option value="global">海外 Vercel</option>
          <option value="cn">国内阿里云</option>
        </select>
        <button id="saveHostedRegion" type="button">保存区域</button>
      </div>
      <div id="languageStatus" class="muted"></div>
      <div id="hostedRegionStatus" class="muted"></div>
      <div class="row">
        <select id="permissionScope">
          <option value="workspace">仅授权文件夹</option>
          <option value="full_computer">整台电脑</option>
        </select>
        <button id="savePermissionScope" type="button">保存权限</button>
      </div>
      <div id="permissionScopeStatus" class="muted"></div>
      <div class="row">
        <select id="terminalAccess">
          <option value="disabled">关闭终端命令</option>
          <option value="enabled">允许终端命令</option>
        </select>
        <button id="saveTerminalAccess" type="button">保存终端权限</button>
      </div>
      <div id="terminalAccessStatus" class="muted"></div>
    </section>
    <section>
      <h2>模型路由</h2>
      <div class="row">
        <select id="route"></select>
        <button id="saveRoute">保存</button>
      </div>
      <div class="row">
        <select id="provider"></select>
        <input id="apiKey" type="password" placeholder="BYO API key" style="flex:1;padding:8px;border:1px solid #bbb;border-radius:6px">
        <button id="saveKey">保存 Key</button>
      </div>
      <div id="keyStatus" class="muted"></div>
	    </section>
	    <section>
	      <h2>文件权限</h2>
	      <div class="muted" style="margin-bottom:8px">建议授权一个专门的工作文件夹，例如 Documents/Auctus Workspace。不要直接授权软件所在目录。</div>
	      <div class="row">
	        <input id="workspacePath" placeholder="授权文件夹路径" style="flex:1;padding:8px;border:1px solid #bbb;border-radius:6px">
	        <button id="openFolderPicker" type="button">选择</button>
	        <button id="saveWorkspace">授权</button>
	      </div>
	      <div id="workspaceStatus" class="muted"></div>
	    </section>
	    <section>
	      <h2>文件</h2>
      <div class="row">
        <input id="file" type="file">
        <button id="upload">上传</button>
      </div>
      <div id="uploadStatus" class="muted"></div>
    </section>
    <section>
      <h2>候选记忆</h2>
      <div id="memories" class="stack"></div>
    </section>
    <section>
      <h2>工具日志</h2>
      <button id="refreshLogs">刷新</button>
      <div id="logs" class="stack" style="margin-top:8px"></div>
    </section>
  </aside>
<div id="onboardingOverlay" class="overlay">
  <div class="wizard">
    <header>
      <h1>首次设置</h1>
      <div class="muted">选择模型接入方式，之后可以在右侧设置里修改。</div>
    </header>
    <form id="onboardingForm">
      <div class="choice-grid">
        <label class="choice"><input type="radio" name="mode" value="own_api" checked><strong>自己的 API</strong><span>填写服务商 key，本地加密保存。</span></label>
        <label class="choice"><input type="radio" name="mode" value="hosted_api"><strong>Auctus 托管 API</strong><span>登录后使用余额和免费额度。</span></label>
        <label class="choice"><input type="radio" name="mode" value="local_model"><strong>本地模型</strong><span>使用 Ollama 或 LM Studio。</span></label>
      </div>
      <div class="field">
        <label for="setupModel">模型</label>
        <select id="setupModel"></select>
      </div>
      <div class="field">
        <label for="setupLanguage">系统语言</label>
        <select id="setupLanguage">
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
      </div>
      <div id="ownApiPanel" class="wizard-panel active">
        <div class="field">
          <label for="setupProvider">服务商</label>
          <select id="setupProvider"></select>
        </div>
        <div class="field">
          <label for="setupApiKey">API key</label>
          <input id="setupApiKey" type="password" autocomplete="off" placeholder="sk-...">
        </div>
        <div class="row">
          <button id="verifySetupKey" type="button">验证 Key</button>
          <div id="setupKeyStatus" class="muted"></div>
        </div>
      </div>
      <div id="hostedApiPanel" class="wizard-panel">
        <div class="field">
          <label for="setupEmail">登录邮箱</label>
          <input id="setupEmail" type="email" autocomplete="email" placeholder="you@example.com">
        </div>
        <div class="field">
          <label for="setupHostedRegion">服务区域</label>
          <select id="setupHostedRegion">
            <option value="auto">自动选择区域</option>
            <option value="global">海外 Vercel</option>
            <option value="cn">国内阿里云</option>
          </select>
        </div>
        <div class="muted">本地占位版会记录登录邮箱和免费额度；真实支付在计费网站阶段接入。</div>
      </div>
      <div id="localModelPanel" class="wizard-panel">
        <div class="muted">本地模型不会走云端计费。请确保本机模型服务已启动，并在模型框中填写对应模型名。</div>
      </div>
      <div class="field">
        <label for="agentName">给 Agent 起个名字</label>
        <input id="agentName" placeholder="可留空">
      </div>
      <div class="field">
        <label for="persona">AI 性格</label>
        <select id="persona">
          <option value="professional">专业简洁</option>
          <option value="cool_sister">高冷御姐</option>
          <option value="warm_uncle">知心大叔</option>
          <option value="reliable_bro">可靠小哥</option>
          <option value="cheerful_girl">元气萌妹</option>
        </select>
      </div>
      <div class="field">
        <label for="userIntro">简短自我介绍</label>
        <textarea id="userIntro" placeholder="可选，例如你的工作、偏好、常用语言或项目背景"></textarea>
      </div>
      <div class="field">
        <label for="setupWorkspacePath">授权工作文件夹</label>
        <div class="row" style="margin:0">
          <input id="setupWorkspacePath" placeholder="建议新建一个专门文件夹，例如 /Users/david/Documents/Auctus Workspace" style="flex:1">
          <button id="setupOpenFolderPicker" type="button">选择</button>
        </div>
        <div class="muted">Agent 只能在这个文件夹里读写文件。建议不要用软件安装目录；整机和 Terminal 权限需要以后单独加更严格的开关。</div>
      </div>
      <div class="field">
        <label for="setupPermissionScope">文件权限范围</label>
        <select id="setupPermissionScope">
          <option value="workspace">仅授权文件夹</option>
          <option value="full_computer">整台电脑</option>
        </select>
        <div class="muted">整台电脑权限会允许 Agent 读取/写入任意路径内支持的文本文件。请只在你确定需要时启用。</div>
      </div>
      <div class="field">
        <label for="setupTerminalAccess">终端命令权限</label>
        <select id="setupTerminalAccess">
          <option value="disabled">关闭终端命令</option>
          <option value="enabled">允许终端命令</option>
        </select>
        <div class="muted">开启后，Agent 只有在你当前消息明确要求执行命令时才会运行终端命令。</div>
      </div>
	        <label class="row"><input id="saveProfile" type="checkbox"> <span>把名字和自我介绍直接写入长期记忆</span></label>
      <div class="wizard-actions">
        <div id="setupError" class="error"></div>
        <button class="primary" type="submit">完成设置</button>
      </div>
    </form>
  </div>
</div>
<div id="folderOverlay" class="folder-overlay">
  <div class="folder-dialog">
    <div class="folder-head">
      <h1>选择工作文件夹</h1>
      <button id="closeFolderPicker" class="icon" type="button" title="关闭" aria-label="关闭">×</button>
    </div>
    <div id="folderPath" class="folder-path"></div>
    <div id="folderList" class="folder-list"></div>
    <div class="folder-actions">
      <button id="folderUp" type="button">上一级</button>
      <button id="chooseFolder" class="primary" type="button">授权此文件夹</button>
    </div>
  </div>
</div>
<script>
const sid = crypto.randomUUID();
const messages = document.getElementById('messages');
const overlay = document.getElementById('onboardingOverlay');
const settingsPanel = document.getElementById('settingsPanel');
const settingsBackdrop = document.getElementById('settingsBackdrop');
const folderOverlay = document.getElementById('folderOverlay');
let folderPickerTarget = 'workspacePath';
let currentFolderPath = '';
let currentLanguage = 'zh';
const UI = {
  zh: {
    settings: '设置',
    close: '关闭',
    model: '模型',
    switchModel: '切换',
    setupAgain: '重新设置',
    saveLanguage: '保存语言',
    languageStatus: '当前语言：中文',
    hostedRegion: '服务区域',
    saveRegion: '保存区域',
    hostedRegionStatus: label => `当前托管 API 区域：${label}`,
    regionSaved: label => `托管 API 服务区域已保存：${label}`,
    permissionScope: '文件权限范围',
    savePermission: '保存权限',
    permissionWorkspace: '仅授权文件夹',
    permissionFull: '整台电脑',
    permissionStatus: label => `当前文件权限：${label}`,
    permissionSaved: label => `文件权限已保存：${label}`,
    terminalAccess: '终端命令权限',
    saveTerminal: '保存终端权限',
    terminalDisabled: '关闭终端命令',
    terminalEnabled: '允许终端命令',
    terminalStatus: label => `当前终端权限：${label}`,
    terminalSaved: label => `终端权限已保存：${label}`,
    terminalRequest: '这个任务需要执行终端命令。是否授权？',
    terminalOnce: '仅本次',
    terminalAlways: '始终允许',
    terminalNo: '拒绝',
    route: '模型路由',
    save: '保存',
    saveKey: '保存 Key',
    verifyKey: '验证 Key',
    verifyingKey: '正在验证...',
    keyValid: '验证成功',
    keyInvalid: '验证失败：',
    noKey: '暂无 BYO key',
    filePermission: '文件权限',
    workspaceHint: '建议授权一个专门的工作文件夹，例如 Documents/Auctus Workspace。不要直接授权软件所在目录。',
    workspacePlaceholder: '授权文件夹路径',
    choose: '选择',
    authorize: '授权',
    files: '文件',
    upload: '上传',
    memories: '候选记忆',
    logs: '工具日志',
    refresh: '刷新',
    messagePlaceholder: '输入任务，例如：读取 test_prd.md 并生成总结',
    send: '发送',
    pending: '问题已收到，正在处理',
    progressSteps: [
      '已收到请求',
      '正在联系模型',
      '正在判断是否需要工具',
      '可能正在读取网页、文件或记忆',
      '正在整理结果',
      '任务较复杂，请再等一下'
    ],
    requestFailed: '请求失败：',
    uploaded: '已上传：',
    noMemories: '暂无候选记忆',
    confirm: '确认',
    reject: '拒绝',
    keySaved: 'API key 已保存，路由已切换到 byo。现在可以再发一条消息测试。',
    workspaceCurrent: '当前授权：',
    workspaceUnset: '未设置',
    workspaceWarning: ' 建议改成软件目录外的专门工作文件夹。',
    workspaceSaved: '文件夹已授权。之后我只能在这个目录里读写文件。',
    firstSetup: '首次设置',
    setupDesc: '选择模型接入方式，之后可以在右侧设置里修改。',
    ownApi: '自己的 API',
    ownApiDesc: '填写服务商 key，本地加密保存。',
    hostedApi: 'Auctus 托管 API',
    hostedApiDesc: '登录后使用余额和免费额度。',
    regionAuto: '自动选择区域',
    regionGlobal: '海外 Vercel',
    regionCn: '国内阿里云',
    localModel: '本地模型',
    localModelDesc: '使用 Ollama 或 LM Studio。',
    systemLanguage: '系统语言',
    provider: '服务商',
    loginEmail: '登录邮箱',
    hostedHint: '本地占位版会记录登录邮箱和免费额度；真实支付在计费网站阶段接入。',
    localHint: '本地模型不会走云端计费。请确保本机模型服务已启动，并在模型框中填写对应模型名。',
    agentName: '给 Agent 起个名字',
    agentNamePlaceholder: '可留空',
    persona: 'AI 性格',
    userIntro: '简短自我介绍',
    userIntroPlaceholder: '可选，例如你的工作、偏好、常用语言或项目背景',
    setupWorkspace: '授权工作文件夹',
    setupWorkspacePlaceholder: '建议新建一个专门文件夹，例如 /Users/david/Documents/Auctus Workspace',
    workspaceHelp: '默认情况下 Agent 只能在这个文件夹里读写文件。建议不要用软件安装目录。',
    permissionHelp: '整台电脑权限会允许 Agent 读取/写入任意路径内支持的文本文件。请只在你确定需要时启用。',
    terminalHelp: '开启后，Agent 只有在你当前消息明确要求执行命令时才会运行终端命令。',
    saveProfile: '把名字和自我介绍直接写入长期记忆',
    finishSetup: '完成设置',
    folderTitle: '选择工作文件夹',
    folderUp: '上一级',
    chooseFolder: '授权此文件夹',
    folderEmpty: '这个文件夹下没有可显示的子文件夹。',
    unreadable: '不可读',
    helloAgent: name => `你好，我是 ${name}。`,
    languageSaved: '系统语言已保存。之后我默认用中文回复。',
    setupDone: '首次设置已完成。你现在可以发第一条任务。',
    hostedPreview: '首次设置已完成。当前没有配置云端 Relay，本地预览会使用 .env 里的平台模型 key；如果仍然认证失败，请点“重新设置”选择“自己的 API”。',
    profileSaved: '知道了，名字和自我介绍已记住。'
  },
  en: {
    settings: 'Settings',
    close: 'Close',
    model: 'Model',
    switchModel: 'Switch',
    setupAgain: 'Setup',
    saveLanguage: 'Save Language',
    languageStatus: 'Current language: English',
    hostedRegion: 'Service Region',
    saveRegion: 'Save Region',
    hostedRegionStatus: label => `Hosted API region: ${label}`,
    regionSaved: label => `Hosted API service region saved: ${label}`,
    permissionScope: 'File Permission Scope',
    savePermission: 'Save Permission',
    permissionWorkspace: 'Authorized Folder Only',
    permissionFull: 'Whole Computer',
    permissionStatus: label => `Current file permission: ${label}`,
    permissionSaved: label => `File permission saved: ${label}`,
    terminalAccess: 'Terminal Command Access',
    saveTerminal: 'Save Terminal Access',
    terminalDisabled: 'Disable Terminal Commands',
    terminalEnabled: 'Allow Terminal Commands',
    terminalStatus: label => `Current terminal access: ${label}`,
    terminalSaved: label => `Terminal access saved: ${label}`,
    terminalRequest: 'This task needs to run a terminal command. Allow it?',
    terminalOnce: 'This Task',
    terminalAlways: 'Always Allow',
    terminalNo: 'No',
    route: 'Model Route',
    save: 'Save',
    saveKey: 'Save Key',
    verifyKey: 'Verify Key',
    verifyingKey: 'Verifying...',
    keyValid: 'Verification successful',
    keyInvalid: 'Verification failed: ',
    noKey: 'No BYO key',
    filePermission: 'File Permission',
    workspaceHint: 'Choose a dedicated workspace folder, such as Documents/Auctus Workspace. Avoid authorizing the app folder itself.',
    workspacePlaceholder: 'Authorized folder path',
    choose: 'Choose',
    authorize: 'Authorize',
    files: 'Files',
    upload: 'Upload',
    memories: 'Memory Candidates',
    logs: 'Tool Logs',
    refresh: 'Refresh',
    messagePlaceholder: 'Type a task, e.g. read test_prd.md and summarize it',
    send: 'Send',
    pending: 'Question received. Working on it',
    progressSteps: [
      'Request received',
      'Contacting the model',
      'Checking whether tools are needed',
      'May be reading webpages, files, or memory',
      'Organizing the result',
      'This is taking longer than usual'
    ],
    requestFailed: 'Request failed: ',
    uploaded: 'Uploaded: ',
    noMemories: 'No candidate memories',
    confirm: 'Confirm',
    reject: 'Reject',
    keySaved: 'API key saved. Route switched to byo. Send another message to test it.',
    workspaceCurrent: 'Authorized folder: ',
    workspaceUnset: 'Not set',
    workspaceWarning: ' Consider using a dedicated workspace outside the app folder.',
    workspaceSaved: 'Folder authorized. I can read and write files only inside this folder.',
    firstSetup: 'First Setup',
    setupDesc: 'Choose how to connect a model. You can change this later in Settings.',
    ownApi: 'Own API',
    ownApiDesc: 'Paste a provider key. It is encrypted locally.',
    hostedApi: 'Auctus Hosted API',
    hostedApiDesc: 'Sign in to use balance and free quota.',
    regionAuto: 'Auto Select',
    regionGlobal: 'Global Vercel',
    regionCn: 'China Aliyun',
    localModel: 'Local Model',
    localModelDesc: 'Use Ollama or LM Studio.',
    systemLanguage: 'System Language',
    provider: 'Provider',
    loginEmail: 'Login Email',
    hostedHint: 'This local preview stores your email and free quota. Real payments will be connected in the billing website phase.',
    localHint: 'Local models do not use cloud billing. Make sure your local model server is running and enter the matching model name.',
    agentName: 'Name Your Agent',
    agentNamePlaceholder: 'Optional',
    persona: 'AI Personality',
    userIntro: 'Short Self Introduction',
    userIntroPlaceholder: 'Optional, such as your work, preferences, usual language, or project background',
    setupWorkspace: 'Authorize Workspace Folder',
    setupWorkspacePlaceholder: 'Create a dedicated folder, e.g. /Users/david/Documents/Auctus Workspace',
    workspaceHelp: 'By default, the Agent can read and write files only inside this folder. Avoid the app install folder.',
    permissionHelp: 'Whole-computer permission allows the Agent to read/write supported text files under any path. Enable it only when you truly need it.',
    terminalHelp: 'When enabled, the Agent runs terminal commands only if your current message explicitly asks for it.',
    saveProfile: 'Save the name and introduction to long-term memory',
    finishSetup: 'Finish Setup',
    folderTitle: 'Choose Workspace Folder',
    folderUp: 'Up One Level',
    chooseFolder: 'Authorize This Folder',
    folderEmpty: 'No visible subfolders in this folder.',
    unreadable: 'Unreadable',
    helloAgent: name => `Hi, I am ${name}.`,
    languageSaved: 'System language saved. I will reply in English by default.',
    setupDone: 'First setup is complete. You can send your first task now.',
    hostedPreview: 'First setup is complete. No cloud Relay is configured, so this local preview will use the platform model key from .env. If authentication still fails, open Setup and choose Own API.',
    profileSaved: 'Got it. The name and self introduction have been saved.'
  }
};
const PERSONA_LABELS = {
  zh: {
    professional: '专业简洁',
    cool_sister: '高冷御姐',
    warm_uncle: '知心大叔',
    reliable_bro: '可靠小哥',
    cheerful_girl: '元气萌妹'
  },
  en: {
    professional: 'Professional',
    cool_sister: 'Cool Big Sister',
    warm_uncle: 'Warm Uncle',
    reliable_bro: 'Reliable Bro',
    cheerful_girl: 'Cheerful Girl'
  }
};
function t(key) {
  return (UI[currentLanguage] && UI[currentLanguage][key]) || UI.zh[key] || key;
}
function regionLabel(region) {
  const labels = {
    auto: t('regionAuto'),
    global: t('regionGlobal'),
    cn: t('regionCn')
  };
  return labels[region] || labels.auto;
}
function permissionLabel(scope) {
  const labels = {
    workspace: t('permissionWorkspace'),
    full_computer: t('permissionFull')
  };
  return labels[scope] || labels.workspace;
}
function terminalLabel(access) {
  const labels = {
    disabled: t('terminalDisabled'),
    enabled: t('terminalEnabled')
  };
  return labels[access] || labels.disabled;
}
function setText(el, value) {
  if (el) el.textContent = value;
}
function setPlaceholder(id, value) {
  const el = document.getElementById(id);
  if (el) el.placeholder = value;
}
function updatePersonaLabels() {
  const labels = PERSONA_LABELS[currentLanguage] || PERSONA_LABELS.zh;
  document.querySelectorAll('#persona option').forEach(opt => {
    opt.textContent = labels[opt.value] || opt.textContent;
  });
}
function updateRegionLabels() {
  ['hostedRegion', 'setupHostedRegion'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
      opt.textContent = regionLabel(opt.value);
    });
  });
}
function updatePermissionLabels() {
  ['permissionScope', 'setupPermissionScope'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
      opt.textContent = permissionLabel(opt.value);
    });
  });
}
function updateTerminalLabels() {
  ['terminalAccess', 'setupTerminalAccess'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
      opt.textContent = terminalLabel(opt.value);
    });
  });
}
function applyLanguage(language) {
  currentLanguage = language === 'en' ? 'en' : 'zh';
  document.documentElement.lang = currentLanguage;
  document.getElementById('systemLanguage').value = currentLanguage;
  document.getElementById('setupLanguage').value = currentLanguage;
  document.getElementById('openSettings').title = t('settings');
  document.getElementById('openSettings').setAttribute('aria-label', t('settings'));
  document.getElementById('closeSettings').title = t('close');
  document.getElementById('closeSettings').setAttribute('aria-label', t('close'));
  settingsPanel.setAttribute('aria-label', t('settings'));
  setPlaceholder('message', t('messagePlaceholder'));
  setText(document.querySelector('#chatForm button.primary'), t('send'));
  setPlaceholder('workspacePath', t('workspacePlaceholder'));
  setPlaceholder('agentName', t('agentNamePlaceholder'));
  setPlaceholder('userIntro', t('userIntroPlaceholder'));
  setPlaceholder('setupWorkspacePath', t('setupWorkspacePlaceholder'));

  setText(document.querySelector('.settings-head h1'), t('settings'));
  const sections = settingsPanel.querySelectorAll('section');
  setText(sections[0]?.querySelector('h2'), t('model'));
  setText(document.getElementById('saveModel'), t('switchModel'));
  setText(document.getElementById('openSetup'), t('setupAgain'));
  setText(document.getElementById('saveLanguage'), t('saveLanguage'));
  setText(document.getElementById('languageStatus'), t('languageStatus'));
  setText(document.getElementById('saveHostedRegion'), t('saveRegion'));
  const selectedRegion = document.getElementById('hostedRegion')?.value || 'auto';
  setText(document.getElementById('hostedRegionStatus'), t('hostedRegionStatus')(regionLabel(selectedRegion)));
  setText(document.getElementById('savePermissionScope'), t('savePermission'));
  const selectedPermission = document.getElementById('permissionScope')?.value || 'workspace';
  setText(document.getElementById('permissionScopeStatus'), t('permissionStatus')(permissionLabel(selectedPermission)));
  setText(document.getElementById('saveTerminalAccess'), t('saveTerminal'));
  const selectedTerminal = document.getElementById('terminalAccess')?.value || 'disabled';
  setText(document.getElementById('terminalAccessStatus'), t('terminalStatus')(terminalLabel(selectedTerminal)));
  setText(sections[1]?.querySelector('h2'), t('route'));
  setText(document.getElementById('saveRoute'), t('save'));
  setText(document.getElementById('saveKey'), t('saveKey'));
  setText(document.getElementById('verifySetupKey'), t('verifyKey'));
  setText(sections[2]?.querySelector('h2'), t('filePermission'));
  setText(sections[2]?.querySelector('.muted'), t('workspaceHint'));
  setText(document.getElementById('openFolderPicker'), t('choose'));
  setText(document.getElementById('saveWorkspace'), t('authorize'));
  setText(sections[3]?.querySelector('h2'), t('files'));
  setText(document.getElementById('upload'), t('upload'));
  setText(sections[4]?.querySelector('h2'), t('memories'));
  setText(sections[5]?.querySelector('h2'), t('logs'));
  setText(document.getElementById('refreshLogs'), t('refresh'));

  setText(document.querySelector('.wizard h1'), t('firstSetup'));
  setText(document.querySelector('.wizard header .muted'), t('setupDesc'));
  const choices = document.querySelectorAll('.choice');
  setText(choices[0]?.querySelector('strong'), t('ownApi'));
  setText(choices[0]?.querySelector('span'), t('ownApiDesc'));
  setText(choices[1]?.querySelector('strong'), t('hostedApi'));
  setText(choices[1]?.querySelector('span'), t('hostedApiDesc'));
  setText(choices[2]?.querySelector('strong'), t('localModel'));
  setText(choices[2]?.querySelector('span'), t('localModelDesc'));
  setText(document.querySelector('label[for="setupModel"]'), t('model'));
  setText(document.querySelector('label[for="setupLanguage"]'), t('systemLanguage'));
  setText(document.querySelector('label[for="setupHostedRegion"]'), t('hostedRegion'));
  setText(document.querySelector('label[for="setupProvider"]'), t('provider'));
  setText(document.querySelector('label[for="setupEmail"]'), t('loginEmail'));
  setText(document.querySelector('#hostedApiPanel .muted'), t('hostedHint'));
  setText(document.querySelector('#localModelPanel .muted'), t('localHint'));
  setText(document.querySelector('label[for="agentName"]'), t('agentName'));
  setText(document.querySelector('label[for="persona"]'), t('persona'));
  setText(document.querySelector('label[for="userIntro"]'), t('userIntro'));
  setText(document.querySelector('label[for="setupWorkspacePath"]'), t('setupWorkspace'));
  setText(document.getElementById('setupOpenFolderPicker'), t('choose'));
  setText(document.querySelector('#setupWorkspacePath')?.closest('.field')?.querySelector('.muted'), t('workspaceHelp'));
  setText(document.querySelector('label[for="setupPermissionScope"]'), t('permissionScope'));
  setText(document.querySelector('#setupPermissionScope')?.closest('.field')?.querySelector('.muted'), t('permissionHelp'));
  setText(document.querySelector('label[for="setupTerminalAccess"]'), t('terminalAccess'));
  setText(document.querySelector('#setupTerminalAccess')?.closest('.field')?.querySelector('.muted'), t('terminalHelp'));
  setText(document.getElementById('saveProfile')?.closest('label')?.querySelector('span'), t('saveProfile'));
  setText(document.querySelector('#onboardingForm button.primary'), t('finishSetup'));
  updatePersonaLabels();
  updateRegionLabels();
  updatePermissionLabels();
  updateTerminalLabels();

  setText(document.querySelector('.folder-head h1'), t('folderTitle'));
  document.getElementById('closeFolderPicker').title = t('close');
  document.getElementById('closeFolderPicker').setAttribute('aria-label', t('close'));
  setText(document.getElementById('folderUp'), t('folderUp'));
  setText(document.getElementById('chooseFolder'), t('chooseFolder'));
}
function openSettingsPanel() {
  settingsPanel.classList.add('open');
  settingsBackdrop.classList.add('open');
}
function closeSettingsPanel() {
  settingsPanel.classList.remove('open');
  settingsBackdrop.classList.remove('open');
}
async function openFolderPicker(targetId) {
  folderPickerTarget = targetId;
  folderOverlay.style.display = 'flex';
  await loadFolder('');
}
function closeFolderPicker() {
  folderOverlay.style.display = 'none';
}
async function loadFolder(path) {
  const url = path ? `/api/folders?path=${encodeURIComponent(path)}` : '/api/folders';
  const r = await fetch(url);
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('folderPath').textContent = j.detail || JSON.stringify(j);
    document.getElementById('folderList').textContent = '';
    return;
  }
  currentFolderPath = j.path;
  document.getElementById('folderPath').textContent = j.path;
  const list = document.getElementById('folderList');
  list.textContent = '';
  (j.items || []).forEach(item => {
    const btn = document.createElement('button');
    btn.className = 'folder-row';
    btn.type = 'button';
    btn.innerHTML = `<span>📁 ${item.name}</span><span class="muted">${item.readable ? '' : t('unreadable')}</span>`;
    btn.onclick = () => loadFolder(item.path);
    list.appendChild(btn);
  });
  if (!j.items || !j.items.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.style.padding = '12px';
    empty.textContent = t('folderEmpty');
    list.appendChild(empty);
  }
}
function scrollChatToBottom() {
  requestAnimationFrame(() => {
    messages.scrollTop = messages.scrollHeight;
    setTimeout(() => { messages.scrollTop = messages.scrollHeight; }, 30);
  });
}
function addMessage(text, cls) {
  const el = document.createElement('div');
  el.className = `msg ${cls}`;
  el.textContent = text;
  messages.appendChild(el);
  scrollChatToBottom();
  return el;
}
function addPendingMessage() {
  const el = document.createElement('div');
  el.className = 'msg agent pending';
  const main = document.createElement('span');
  main.className = 'pending-main';
  const text = document.createElement('span');
  text.textContent = t('pending');
  const dots = document.createElement('span');
  dots.className = 'typing';
  dots.innerHTML = '<span></span><span></span><span></span>';
  main.append(text, dots);
  const detail = document.createElement('span');
  detail.className = 'pending-detail';
  const steps = t('progressSteps');
  const startedAt = Date.now();
  let stepIndex = 0;
  const updateDetail = () => {
    const elapsed = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    detail.textContent = `${steps[Math.min(stepIndex, steps.length - 1)]} · ${elapsed}s`;
    if (stepIndex < steps.length - 1) stepIndex += 1;
    scrollChatToBottom();
  };
  updateDetail();
  el._progressTimer = setInterval(updateDetail, 3500);
  el.append(main, detail);
  messages.appendChild(el);
  scrollChatToBottom();
  return el;
}
function replaceMessage(el, text, cls='agent') {
  if (el._progressTimer) {
    clearInterval(el._progressTimer);
    el._progressTimer = null;
  }
  el.className = `msg ${cls}`;
  el.textContent = text;
  scrollChatToBottom();
}
function addFileLink(path) {
  const a = document.createElement('a');
  a.className = 'file';
  a.href = path;
  a.target = '_blank';
  a.textContent = path.split('/').pop();
  messages.appendChild(a);
  scrollChatToBottom();
}
async function sendChat(text, terminalPermission='') {
  const input = document.getElementById('message');
  const sendButton = document.querySelector('#chatForm button.primary');
  input.disabled = true;
  sendButton.disabled = true;
  const pending = addPendingMessage();
  try {
    const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({session_id: sid, message: text, terminal_permission: terminalPermission})});
    const j = await r.json();
    if (!r.ok) {
      replaceMessage(pending, j.detail || JSON.stringify(j));
      return;
    }
    if (j.permission_request && j.permission_request.type === 'terminal') {
      replaceMessage(pending, t('terminalRequest'));
      addTerminalPermissionButtons(text);
      return;
    }
    replaceMessage(pending, j.reply || JSON.stringify(j));
    (j.files || []).forEach(addFileLink);
    scrollChatToBottom();
    refreshLogs();
    refreshMemories();
  } catch (err) {
    replaceMessage(pending, `${t('requestFailed')}${err}`);
  } finally {
    input.disabled = false;
    sendButton.disabled = false;
    input.focus();
  }
}
document.getElementById('chatForm').onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById('message');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMessage(text, 'user');
  await sendChat(text);
};
function addTerminalPermissionButtons(text) {
  const el = document.createElement('div');
  el.className = 'msg agent';
  const row = document.createElement('div');
  row.className = 'row';
  const once = document.createElement('button');
  once.textContent = t('terminalOnce');
  once.onclick = async () => {
    el.remove();
    await sendChat(text, 'once');
  };
  const always = document.createElement('button');
  always.textContent = t('terminalAlways');
  always.onclick = async () => {
    await saveTerminalAccessValue('enabled', false);
    el.remove();
    await sendChat(text, 'always');
  };
  const no = document.createElement('button');
  no.textContent = t('terminalNo');
  no.onclick = () => el.remove();
  row.append(once, always, no);
  el.append(row);
  messages.appendChild(el);
  scrollChatToBottom();
}
document.getElementById('upload').onclick = async () => {
  const f = document.getElementById('file').files[0];
  if (!f) return;
  const body = await f.arrayBuffer();
  const r = await fetch(`/api/upload?filename=${encodeURIComponent(f.name)}`, {method:'POST', body});
  const j = await r.json();
  document.getElementById('uploadStatus').textContent = j.filename ? `${t('uploaded')}${j.filename}` : JSON.stringify(j);
};
async function refreshLogs() {
  const r = await fetch('/api/logs?tail=8');
  const j = await r.json();
  const box = document.getElementById('logs');
  box.textContent = '';
  (j.items || []).forEach(x => {
    const el = document.createElement('div');
    el.className = 'item';
    el.textContent = `${x.ts || ''} ${x.tool_name || ''} ${x.status || ''}`;
    box.appendChild(el);
  });
}
async function refreshMemories() {
  const r = await fetch('/api/memories?candidates=true');
  const j = await r.json();
  const box = document.getElementById('memories');
  box.textContent = '';
  if (!j.items || !j.items.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = t('noMemories');
    box.appendChild(empty);
    return;
  }
  j.items.forEach(m => {
    const el = document.createElement('div');
    el.className = 'item';
    const title = document.createElement('strong');
    title.textContent = `[${m.type}] ${m.title}`;
    const content = document.createElement('div');
    content.textContent = m.content;
    content.className = 'muted';
    const row = document.createElement('div');
    row.className = 'row';
    const ok = document.createElement('button');
    ok.textContent = t('confirm');
    ok.onclick = () => updateMemory(m.id, 'confirm');
    const no = document.createElement('button');
    no.textContent = t('reject');
    no.onclick = () => updateMemory(m.id, 'reject');
    row.append(ok, no);
    el.append(title, content, row);
    box.appendChild(el);
  });
}
async function updateMemory(id, action) {
  await fetch(`/api/memories/${id}/${action}`, {method:'POST'});
  refreshMemories();
}
async function loadModels() {
  const r = await fetch('/api/model');
  const j = await r.json();
  const sel = document.getElementById('model');
  const setupSel = document.getElementById('setupModel');
  sel.textContent = '';
  setupSel.textContent = '';
  (j.available || [j.model]).forEach(m => {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m; opt.selected = m === j.model;
    sel.appendChild(opt);
    const setupOpt = opt.cloneNode(true);
    setupSel.appendChild(setupOpt);
  });
}
document.getElementById('saveModel').onclick = async () => {
  const model = document.getElementById('model').value;
  await fetch('/api/model', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model})});
  loadModels();
};
async function loadLanguage() {
  const r = await fetch('/api/language');
  const j = await r.json();
  const language = j.language || 'zh';
  document.getElementById('systemLanguage').value = language;
  document.getElementById('setupLanguage').value = language;
  applyLanguage(language);
}
async function loadHostedRegion() {
  const r = await fetch('/api/hosted-region');
  const j = await r.json();
  const region = j.region || 'auto';
  document.getElementById('hostedRegion').value = region;
  document.getElementById('setupHostedRegion').value = region;
  document.getElementById('hostedRegionStatus').textContent = t('hostedRegionStatus')(regionLabel(region));
}
async function detectHostedRegion() {
  const current = document.getElementById('hostedRegion').value || 'auto';
  if (current !== 'auto') return;
  const payload = {
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    locale: navigator.language || '',
    languages: Array.from(navigator.languages || [])
  };
  const r = await fetch('/api/hosted-region/detect', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const j = await r.json();
  if (!r.ok || !j.region) return;
  document.getElementById('hostedRegion').value = j.region;
  document.getElementById('setupHostedRegion').value = j.region;
  document.getElementById('hostedRegionStatus').textContent = t('hostedRegionStatus')(regionLabel(j.region));
}
async function loadPermissionScope() {
  const r = await fetch('/api/permission-scope');
  const j = await r.json();
  const scope = j.scope || 'workspace';
  document.getElementById('permissionScope').value = scope;
  document.getElementById('setupPermissionScope').value = scope;
  document.getElementById('permissionScopeStatus').textContent = t('permissionStatus')(permissionLabel(scope));
}
async function loadTerminalAccess() {
  const r = await fetch('/api/terminal-access');
  const j = await r.json();
  const access = j.access || 'disabled';
  document.getElementById('terminalAccess').value = access;
  document.getElementById('setupTerminalAccess').value = access;
  document.getElementById('terminalAccessStatus').textContent = t('terminalStatus')(terminalLabel(access));
}
document.getElementById('saveLanguage').onclick = async () => {
  const language = document.getElementById('systemLanguage').value;
  const r = await fetch('/api/language', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({language})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('languageStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('setupLanguage').value = j.language;
  applyLanguage(j.language);
  addMessage(t('languageSaved'), 'agent');
};
document.getElementById('saveHostedRegion').onclick = async () => {
  const region = document.getElementById('hostedRegion').value;
  const r = await fetch('/api/hosted-region', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({region})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('hostedRegionStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('setupHostedRegion').value = j.region;
  document.getElementById('hostedRegionStatus').textContent = t('hostedRegionStatus')(regionLabel(j.region));
  addMessage(t('regionSaved')(regionLabel(j.region)), 'agent');
};
document.getElementById('savePermissionScope').onclick = async () => {
  const scope = document.getElementById('permissionScope').value;
  const r = await fetch('/api/permission-scope', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({scope})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('permissionScopeStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('setupPermissionScope').value = j.scope;
  document.getElementById('permissionScopeStatus').textContent = t('permissionStatus')(permissionLabel(j.scope));
  addMessage(t('permissionSaved')(permissionLabel(j.scope)), 'agent');
};
async function saveTerminalAccessValue(access, announce=true) {
  const r = await fetch('/api/terminal-access', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({access})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('terminalAccessStatus').textContent = j.detail || JSON.stringify(j);
    return false;
  }
  document.getElementById('terminalAccess').value = j.access;
  document.getElementById('setupTerminalAccess').value = j.access;
  document.getElementById('terminalAccessStatus').textContent = t('terminalStatus')(terminalLabel(j.access));
  if (announce) addMessage(t('terminalSaved')(terminalLabel(j.access)), 'agent');
  return true;
}
document.getElementById('saveTerminalAccess').onclick = async () => {
  await saveTerminalAccessValue(document.getElementById('terminalAccess').value);
};
async function loadRoute() {
  const r = await fetch('/api/route');
  const j = await r.json();
  const sel = document.getElementById('route');
  sel.textContent = '';
  (j.available || ['local','byo','proxy']).forEach(route => {
    const opt = document.createElement('option');
    opt.value = route; opt.textContent = route; opt.selected = route === j.route;
    sel.appendChild(opt);
  });
  const keys = (j.api_keys || []).map(k => `${k.provider}: ${k.key_hint}`).join('  ');
  document.getElementById('keyStatus').textContent = keys || t('noKey');
  loadProviders();
}
async function loadProviders() {
  const r = await fetch('/api/api-keys');
  const j = await r.json();
  const sel = document.getElementById('provider');
  const setupSel = document.getElementById('setupProvider');
  sel.textContent = '';
  setupSel.textContent = '';
  (j.providers || ['anthropic','openai','deepseek']).forEach(p => {
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = p;
    sel.appendChild(opt);
    setupSel.appendChild(opt.cloneNode(true));
  });
}
document.getElementById('saveRoute').onclick = async () => {
  const route = document.getElementById('route').value;
  await fetch('/api/route', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({route})});
  loadRoute();
};
document.getElementById('saveKey').onclick = async () => {
  const provider = document.getElementById('provider').value;
  const api_key = document.getElementById('apiKey').value.trim();
  if (!api_key) return;
  const valid = await validateApiKey({
    provider,
    api_key,
    model: document.getElementById('model').value,
    statusId: 'keyStatus',
    buttonId: 'saveKey'
  });
  if (!valid) return;
  const r = await fetch('/api/api-keys', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({provider, api_key})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('keyStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  await fetch('/api/route', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({route: 'byo'})});
  document.getElementById('apiKey').value = '';
  loadRoute();
  addMessage(t('keySaved'), 'agent');
};
async function validateApiKey({provider, api_key, model, statusId, buttonId}) {
  const status = document.getElementById(statusId);
  const button = document.getElementById(buttonId);
  if (!provider || !api_key || !model) return false;
  status.textContent = t('verifyingKey');
  if (button) button.disabled = true;
  try {
    const r = await fetch('/api/api-keys/validate', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({provider, api_key, model})
    });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      status.textContent = `${t('keyInvalid')}${j.detail || j.error || JSON.stringify(j)}`;
      return false;
    }
    status.textContent = t('keyValid');
    return true;
  } catch (err) {
    status.textContent = `${t('keyInvalid')}${err}`;
    return false;
  } finally {
    if (button) button.disabled = false;
  }
}
async function loadWorkspace() {
  const r = await fetch('/api/workspace');
  const j = await r.json();
  document.getElementById('workspacePath').value = j.workspace || '';
  document.getElementById('setupWorkspacePath').value = j.workspace || '';
  const warning = j.uses_default ? t('workspaceWarning') : '';
  document.getElementById('workspaceStatus').textContent = `${t('workspaceCurrent')}${j.workspace || t('workspaceUnset')}${warning}`;
}
document.getElementById('saveWorkspace').onclick = async () => {
  const path = document.getElementById('workspacePath').value.trim();
  if (!path) return;
  const r = await fetch('/api/workspace', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('workspaceStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('workspaceStatus').textContent = `${t('workspaceCurrent')}${j.workspace}`;
  addMessage(t('workspaceSaved'), 'agent');
};
document.getElementById('openFolderPicker').onclick = () => openFolderPicker('workspacePath');
document.getElementById('setupOpenFolderPicker').onclick = () => openFolderPicker('setupWorkspacePath');
document.getElementById('verifySetupKey').onclick = async () => validateApiKey({
  provider: document.getElementById('setupProvider').value,
  api_key: document.getElementById('setupApiKey').value.trim(),
  model: document.getElementById('setupModel').value,
  statusId: 'setupKeyStatus',
  buttonId: 'verifySetupKey'
});
document.getElementById('systemLanguage').onchange = (e) => applyLanguage(e.target.value);
document.getElementById('setupLanguage').onchange = (e) => applyLanguage(e.target.value);
document.getElementById('closeFolderPicker').onclick = closeFolderPicker;
document.getElementById('folderUp').onclick = () => loadFolder(`${currentFolderPath}/..`);
document.getElementById('chooseFolder').onclick = () => {
  document.getElementById(folderPickerTarget).value = currentFolderPath;
  closeFolderPicker();
};
folderOverlay.addEventListener('click', (e) => {
  if (e.target === folderOverlay) closeFolderPicker();
});
function updateSetupPanels() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  document.getElementById('ownApiPanel').classList.toggle('active', mode === 'own_api');
  document.getElementById('hostedApiPanel').classList.toggle('active', mode === 'hosted_api');
  document.getElementById('localModelPanel').classList.toggle('active', mode === 'local_model');
}
document.querySelectorAll('input[name="mode"]').forEach(x => x.onchange = updateSetupPanels);
document.getElementById('openSettings').onclick = openSettingsPanel;
document.getElementById('closeSettings').onclick = closeSettingsPanel;
settingsBackdrop.onclick = closeSettingsPanel;
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && folderOverlay.style.display === 'flex') {
    closeFolderPicker();
    return;
  }
  if (e.key === 'Escape') closeSettingsPanel();
});
document.getElementById('openSetup').onclick = () => {
  closeSettingsPanel();
  overlay.style.display = 'flex';
  updateSetupPanels();
};
async function loadOnboarding() {
  const r = await fetch('/api/onboarding');
  const j = await r.json();
  document.getElementById('persona').value = j.persona || 'professional';
  document.getElementById('systemLanguage').value = j.system_language || 'zh';
  document.getElementById('setupLanguage').value = j.system_language || 'zh';
  document.getElementById('hostedRegion').value = j.hosted_region || 'auto';
  document.getElementById('setupHostedRegion').value = j.hosted_region || 'auto';
  document.getElementById('permissionScope').value = j.permission_scope || 'workspace';
  document.getElementById('setupPermissionScope').value = j.permission_scope || 'workspace';
  document.getElementById('terminalAccess').value = j.terminal_access || 'disabled';
  document.getElementById('setupTerminalAccess').value = j.terminal_access || 'disabled';
  applyLanguage(j.system_language || 'zh');
  if (!j.completed) {
    overlay.style.display = 'flex';
  } else if (j.agent_name) {
    addMessage(t('helloAgent')(j.agent_name), 'agent');
  }
}
document.getElementById('onboardingForm').onsubmit = async (e) => {
  e.preventDefault();
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const payload = {
    mode,
    model: document.getElementById('setupModel').value,
    system_language: document.getElementById('setupLanguage').value,
    hosted_region: document.getElementById('setupHostedRegion').value,
    permission_scope: document.getElementById('setupPermissionScope').value,
    terminal_access: document.getElementById('setupTerminalAccess').value,
    provider: document.getElementById('setupProvider').value,
    api_key: document.getElementById('setupApiKey').value.trim(),
    hosted_email: document.getElementById('setupEmail').value.trim(),
    agent_name: document.getElementById('agentName').value.trim(),
    persona: document.getElementById('persona').value,
    user_intro: document.getElementById('userIntro').value.trim(),
    workspace_path: document.getElementById('setupWorkspacePath').value.trim(),
    save_profile: document.getElementById('saveProfile').checked
  };
  const error = document.getElementById('setupError');
  error.textContent = '';
  if (mode === 'own_api') {
    const valid = await validateApiKey({
      provider: payload.provider,
      api_key: payload.api_key,
      model: payload.model,
      statusId: 'setupKeyStatus',
      buttonId: 'verifySetupKey'
    });
    if (!valid) return;
  }
  const r = await fetch('/api/onboarding', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const j = await r.json();
  if (!r.ok) {
    error.textContent = j.detail || JSON.stringify(j);
    return;
  }
  overlay.style.display = 'none';
  applyLanguage(j.system_language || payload.system_language || currentLanguage);
  if (mode === 'hosted_api' && j.route === 'local') {
    addMessage(t('hostedPreview'), 'agent');
  } else {
    addMessage(t('setupDone'), 'agent');
  }
	  if (j.candidate_memory_ids && j.candidate_memory_ids.length) {
	    addMessage(t('profileSaved'), 'agent');
	  }
  loadModels();
  loadLanguage();
  loadRoute();
  refreshMemories();
};
document.getElementById('refreshLogs').onclick = refreshLogs;
async function init() {
  await loadLanguage();
  await loadHostedRegion();
  await detectHostedRegion();
  await loadPermissionScope();
  await loadTerminalAccess();
  await loadModels();
  await loadRoute();
  await loadWorkspace();
  await refreshLogs();
  await refreshMemories();
  await loadOnboarding();
}
init();
</script>
</body>
</html>
"""


MARKETING_CSS = """
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#fbfbf8;color:#1f2933;line-height:1.6}
a{color:#0f5f8f;text-decoration:none}
.wrap{max-width:1120px;margin:0 auto;padding:0 22px}
nav{height:58px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e4e0d6;background:#fff}
.brand{font-weight:700;letter-spacing:.01em}
.navlinks{display:flex;gap:18px;font-size:14px}
.hero{min-height:560px;display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,520px);gap:44px;align-items:center}
h1{font-size:56px;line-height:1.02;margin:0 0 18px;letter-spacing:0}
.lead{font-size:19px;color:#52606d;max-width:640px;margin:0 0 28px}
.actions{display:flex;gap:12px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;border:1px solid #1f2933;border-radius:6px;padding:10px 14px;font-weight:600;background:#1f2933;color:#fff}
.btn.secondary{background:#fff;color:#1f2933}
.product{border:1px solid #d8d2c2;background:#fff;border-radius:8px;box-shadow:0 18px 50px rgba(31,41,51,.13);overflow:hidden}
.bar{height:38px;background:#f0ede5;border-bottom:1px solid #d8d2c2;display:flex;align-items:center;gap:7px;padding:0 12px}
.dot{width:10px;height:10px;border-radius:50%;background:#9aa5b1}
.screen{display:grid;grid-template-columns:1fr 170px;min-height:330px}
.chat{padding:18px;background:#fff}
.msg{border:1px solid #e4e0d6;border-radius:6px;padding:10px 12px;margin-bottom:10px;background:#fbfbf8;font-size:14px}
.msg.user{background:#e9f4fb;margin-left:36px}
.side{border-left:1px solid #e4e0d6;background:#f8f6ef;padding:14px}
.pill{border:1px solid #d8d2c2;background:#fff;border-radius:6px;padding:8px;margin-bottom:8px;font-size:13px}
.band{border-top:1px solid #e4e0d6;padding:54px 0}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
.card{border:1px solid #e4e0d6;background:#fff;border-radius:8px;padding:18px}
h2{font-size:30px;margin:0 0 18px}
h3{margin:0 0 8px;font-size:17px}
.muted{color:#65737f}
.legal{max-width:860px;padding:34px 22px 64px}
.legal h1{font-size:38px}
.legal h2{font-size:22px;margin-top:30px}
footer{border-top:1px solid #e4e0d6;padding:24px 0;color:#65737f;font-size:14px}
@media(max-width:860px){.hero{grid-template-columns:1fr;min-height:auto;padding:44px 0}.screen{grid-template-columns:1fr}h1{font-size:40px}.grid{grid-template-columns:1fr}.navlinks{gap:10px}}
</style>
"""


LANDING_HTML = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auctus Agent</title>{MARKETING_CSS}</head>
<body>
<nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%">
<div class="brand">Auctus Agent</div><div class="navlinks"><a href="/">App</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></div>
</div></nav>
<main>
<section class="wrap hero">
<div>
<h1>Auctus Agent</h1>
<p class="lead">本地优先的个人 AI Agent：处理文件、生成报告和表格、维护长期记忆，并从确认过的历史任务中学习你的工作方式。</p>
<div class="actions"><a class="btn" href="/">打开本地 App</a><a class="btn secondary" href="/files/2026-05-13_cost_report.md">查看成本日报</a></div>
</div>
<div class="product" aria-label="Auctus Agent product preview">
<div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="muted" style="font-size:13px">127.0.0.1:8000</span></div>
<div class="screen">
<div class="chat">
<div class="msg user">读取 PRD，生成总结、功能清单和测试用例。</div>
<div class="msg">已生成 Markdown 报告、Excel 表格和网页原型。输出路径已列出。</div>
<div class="msg user">以后这种任务按同样流程处理。</div>
<div class="msg">已提炼为候选学习项，确认后会进入运行时规则。</div>
</div>
<div class="side"><div class="pill">Memory candidates</div><div class="pill">Tool logs</div><div class="pill">Route: local / byo / proxy</div><div class="pill">Cost report ready</div></div>
</div></div>
</section>
<section class="band"><div class="wrap"><h2>核心能力</h2><div class="grid">
<div class="card"><h3>文件工作流</h3><p class="muted">读取输入文件，生成 Markdown、Excel、HTML 原型和结构化输出。</p></div>
<div class="card"><h3>长期记忆</h3><p class="muted">偏好、项目背景和规则进入候选记忆，经确认后才会生效。</p></div>
<div class="card"><h3>自我进化</h3><p class="muted">从历史对话和工具错误中提炼 workflow、prompt rule 和 retry hint。</p></div>
<div class="card"><h3>成本可见</h3><p class="muted">记录模型调用、token 和 cost，支持每日成本 Markdown 报告。</p></div>
<div class="card"><h3>本地优先</h3><p class="muted">SQLite、Chroma、日志和输出文件默认保存在本机目录。</p></div>
<div class="card"><h3>多模型路由</h3><p class="muted">支持 local、BYO key 和 proxy 路由，便于自用和商业化扩展。</p></div>
</div></div></section>
</main><footer><div class="wrap">Auctus Agent · Local-first personal AI agent</div></footer>
</body></html>"""


PRIVACY_HTML = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy - Auctus Agent</title>{MARKETING_CSS}</head><body>
<nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%"><div class="brand">Auctus Agent</div><div class="navlinks"><a href="/landing">Landing</a><a href="/">App</a><a href="/terms">Terms</a></div></div></nav>
<main class="wrap legal">
<h1>隐私政策</h1>
<p class="muted">最后更新：2026-05-13</p>
<p>Auctus Agent 是本地优先的个人 AI Agent。默认情况下，对话历史、长期记忆、工具日志、成本记录和生成文件保存在你的本机工作目录中。</p>
<h2>我们处理哪些数据</h2>
<p>系统可能处理你输入的消息、上传文件、生成文件、工具调用日志、模型调用用量、API key 配置、候选记忆和已确认记忆。</p>
<h2>数据存放位置</h2>
<p>默认存放在本机的 <code>data/</code>、<code>outputs/</code>、<code>inputs/</code> 和 <code>logs/</code>。如果你启用 Cloud Relay、Proxy 或第三方模型 API，相关请求内容会按配置发送到对应服务。</p>
<h2>API Key</h2>
<p>BYO API key 会在本地加密保存。你也可以只通过环境变量提供 key。请不要把包含 key 的 <code>.env</code> 文件提交到公开仓库。</p>
<h2>模型服务</h2>
<p>当你使用 OpenAI、Anthropic、DeepSeek、DashScope、Ollama 或其他模型提供商时，模型请求会受对应提供商的隐私条款约束。</p>
<h2>用户控制</h2>
<p>你可以删除记忆、拒绝候选记忆、停用或回滚自我学习项，也可以直接删除本机数据目录中的运行数据。</p>
<h2>联系我们</h2>
<p>如果你在发布版本中使用 Auctus Agent，请在这里补充运营主体和联系邮箱。</p>
</main></body></html>"""


TERMS_HTML = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terms of Use - Auctus Agent</title>{MARKETING_CSS}</head><body>
<nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%"><div class="brand">Auctus Agent</div><div class="navlinks"><a href="/landing">Landing</a><a href="/">App</a><a href="/privacy">Privacy</a></div></div></nav>
<main class="wrap legal">
<h1>用户协议</h1>
<p class="muted">最后更新：2026-05-13</p>
<h2>服务说明</h2>
<p>Auctus Agent 提供本地 AI Agent、文件处理、记忆管理、模型路由、成本统计和自动学习辅助功能。具体能力取决于你的本地环境、模型配置和启用的第三方服务。</p>
<h2>用户责任</h2>
<p>你应确保输入、上传和生成内容拥有合法使用权限，并自行负责 API key、访问令牌和本地数据的安全。</p>
<h2>AI 输出</h2>
<p>AI 输出可能不准确或不完整。你应在用于法律、医疗、财务、商业发布或其他高风险场景前自行审核。</p>
<h2>禁止事项</h2>
<p>不得使用 Auctus Agent 进行违法、侵权、绕过访问控制、泄露敏感凭据、攻击系统或违反第三方服务条款的活动。</p>
<h2>第三方服务</h2>
<p>当你配置模型提供商、Telegram、Cloud Relay 或其他服务时，你同时受对应第三方服务条款约束。</p>
<h2>免责声明</h2>
<p>Auctus Agent 按现状提供。除适用法律另有要求外，不对服务连续性、输出准确性、数据丢失或间接损失作保证。</p>
<h2>变更</h2>
<p>发布版本的条款可能随功能、收费方式和运营主体变化而更新。</p>
</main></body></html>"""


class ChatIn(BaseModel):
    session_id: Optional[str] = None
    message: str
    terminal_permission: Optional[str] = None


class ChatOut(BaseModel):
    session_id: str
    reply: str
    files: list[str] = []
    permission_request: Optional[dict] = None


class ModelIn(BaseModel):
    model: str


class RouteIn(BaseModel):
    route: str


class LanguageIn(BaseModel):
    language: str


class HostedRegionIn(BaseModel):
    region: str


class HostedRegionDetectIn(BaseModel):
    timezone: Optional[str] = None
    locale: Optional[str] = None
    languages: list[str] = []


class PermissionScopeIn(BaseModel):
    scope: str


class TerminalAccessIn(BaseModel):
    access: str


class ApiKeyIn(BaseModel):
    provider: str
    api_key: str


class ApiKeyValidationIn(BaseModel):
    provider: str
    api_key: str
    model: str


class WorkspaceIn(BaseModel):
    path: str


class OnboardingIn(BaseModel):
    mode: str
    model: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    hosted_email: Optional[str] = None
    hosted_region: Optional[str] = None
    agent_name: Optional[str] = None
    persona: Optional[str] = None
    system_language: Optional[str] = None
    user_intro: Optional[str] = None
    workspace_path: Optional[str] = None
    permission_scope: Optional[str] = None
    terminal_access: Optional[str] = None
    save_profile: bool = False


class MemoryListOut(BaseModel):
    items: list[dict]


@app.get("/", response_class=HTMLResponse)
def index():
    return WEB_UI


@app.get("/landing", response_class=HTMLResponse)
def landing():
    return LANDING_HTML


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return PRIVACY_HTML


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return TERMS_HTML


@app.get("/billing", response_class=HTMLResponse)
def billing():
    account = accounting.hosted_account_summary()
    email = account["email"] or "未登录"
    balance = account["balance_cents"] / 100
    region = _hosted_region_label(account.get("region", "auto"))
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Auctus Billing</title>{MARKETING_CSS}</head><body>
    <nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%"><div class="brand">Auctus Billing</div><div class="navlinks"><a href="/">App</a><a href="/landing">Landing</a></div></div></nav>
    <main class="wrap legal"><h1>充值与计费</h1>
    <p class="muted">当前账号：{email}</p>
    <p class="muted">服务区域：{region}</p>
    <p>余额：${balance:.2f} · 免费额度：{account["free_tokens"]} tokens</p>
    <h2>当前状态</h2>
    <p>这是托管 API 计费页面的本地占位版。真实支付接入后，这里会显示套餐、订单、支付状态和账单流水。</p>
    <h2>下一步</h2>
    <p>接入支付回调、订单表、余额扣减和管理后台后，Auctus 托管 API 才能用于生产计费。</p>
    </main></body></html>"""


@app.get("/api/onboarding")
def onboarding_state() -> dict:
    state = accounting.get_setup_state()
    _restore_workspace_from_state(state)
    if state.get("model"):
        settings.model = state["model"]
    if state.get("onboarding_mode"):
        _restore_route_from_onboarding_mode(state["onboarding_mode"])
    return {
        "completed": state.get("onboarding_completed") == "1",
        "mode": state.get("onboarding_mode", ""),
        "agent_name": state.get("agent_name", ""),
        "persona": state.get("persona", "professional"),
        "system_language": state.get("system_language", "zh"),
        "hosted_region": _normalize_hosted_region(state.get("hosted_region")),
        "permission_scope": _normalize_permission_scope(state.get("permission_scope")),
        "terminal_access": _normalize_terminal_access(state.get("terminal_access")),
        "hosted_account": accounting.hosted_account_summary(state),
        "route": accounting.current_route(),
        "model": settings.model,
    }


@app.post("/api/onboarding")
def save_onboarding(body: OnboardingIn) -> dict:
    mode = body.mode.strip().lower()
    if mode not in {"own_api", "hosted_api", "local_model"}:
        raise HTTPException(400, "unsupported onboarding mode")

    model = (body.model or "").strip()
    if model:
        settings.model = model

    hosted_account = accounting.hosted_account_summary()
    if mode == "own_api":
        if not body.provider or not body.api_key:
            raise HTTPException(400, "provider and api_key are required")
        accounting.set_api_key(body.provider, body.api_key)
        accounting.set_route("byo")
    elif mode == "hosted_api":
        if not body.hosted_email:
            raise HTTPException(400, "hosted_email is required")
        region = _normalize_hosted_region(body.hosted_region)
        hosted_account = accounting.save_hosted_account(body.hosted_email, region=region)
        accounting.set_route(_hosted_api_route())
    else:
        accounting.set_route("local")

    workspace_dir = _set_workspace_path(body.workspace_path) if body.workspace_path else settings.workspace_dir.resolve()
    candidate_ids = _store_onboarding_memory_candidates(body)
    state = accounting.set_setup_state(
        {
            "onboarding_completed": "1",
            "onboarding_mode": mode,
            "agent_name": (body.agent_name or "").strip(),
            "persona": _normalize_persona(body.persona),
            "system_language": _normalize_language(body.system_language),
            "hosted_region": _normalize_hosted_region(body.hosted_region),
            "permission_scope": _normalize_permission_scope(body.permission_scope),
            "terminal_access": _normalize_terminal_access(body.terminal_access),
            "model": settings.model,
            "workspace_dir": str(workspace_dir),
        }
    )
    return {
        "completed": True,
        "mode": mode,
        "route": accounting.current_route(),
        "model": settings.model,
        "persona": _normalize_persona(body.persona),
        "system_language": _normalize_language(body.system_language),
        "hosted_region": _normalize_hosted_region(body.hosted_region),
        "permission_scope": _normalize_permission_scope(body.permission_scope),
        "terminal_access": _normalize_terminal_access(body.terminal_access),
        "workspace": str(workspace_dir),
        "hosted_account": hosted_account,
        "candidate_memory_ids": candidate_ids,
        "state": state,
    }


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn) -> ChatOut:
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    sid = body.session_id or f"web-{uuid.uuid4().hex[:8]}"
    terminal_permission = _normalize_terminal_permission(body.terminal_permission)
    if _should_request_terminal_permission(body.message, terminal_permission):
        return ChatOut(
            session_id=sid,
            reply="",
            permission_request={
                "type": "terminal",
                "message": "这个任务需要执行终端命令。是否授权？",
                "options": ["once", "always", "no"],
            },
        )
    if terminal_permission == "no":
        return ChatOut(session_id=sid, reply="好的，这次不执行终端命令。")
    try:
        if terminal_permission in {"once", "always"}:
            if terminal_permission == "always":
                accounting.set_setup_state({"terminal_access": "enabled"})
            with tools.terminal_access_override("enabled"):
                result = agent.chat(sid, body.message)
        else:
            result = agent.chat(sid, body.message)
    except Exception as e:
        raise HTTPException(503, _friendly_runtime_error(str(e)))
    files = [_file_url(p) for p in result.get("files", [])]
    return ChatOut(session_id=sid, reply=result["reply"], files=files)


@app.post("/api/upload")
async def upload_file(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=180),
) -> dict:
    safe_name = _safe_input_filename(filename)
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    target = (settings.workspace_dir / safe_name).resolve()
    workspace = settings.workspace_dir.resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        raise HTTPException(400, "invalid filename")
    target.write_bytes(body)
    return {"filename": safe_name, "size": len(body), "path": str(target)}


@app.get("/api/logs")
def logs(tail: int = Query(50, ge=1, le=500)) -> dict:
    log_path = settings.logs_dir / "tool_calls.jsonl"
    if not log_path.exists():
        return {"items": []}
    raw_lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    items: list[dict] = []
    for raw in raw_lines[-tail:]:
        try:
            items.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return {"items": items}


@app.get("/api/memories", response_model=MemoryListOut)
def memories(candidates: bool = False, type: Optional[str] = None) -> MemoryListOut:
    items = memory.list_memories(type=type, confirmed=0 if candidates else 1)
    return MemoryListOut(items=items)


@app.post("/api/memories/{memory_id}/confirm")
def confirm_memory(memory_id: str) -> dict:
    result = memory.confirm_memory(memory_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "memory not found"))
    return result


@app.post("/api/memories/{memory_id}/reject")
def reject_memory(memory_id: str) -> dict:
    result = memory.reject_memory(memory_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "memory not found"))
    return result


@app.get("/api/model")
def get_model() -> dict:
    return {"model": settings.model, "available": _available_models()}


@app.post("/api/model")
def set_model(body: ModelIn) -> dict:
    model = body.model.strip()
    if not model:
        raise HTTPException(400, "empty model")
    settings.model = model
    return {"model": settings.model, "available": _available_models()}


@app.get("/api/language")
def get_language() -> dict:
    state = accounting.get_setup_state()
    return {"language": _normalize_language(state.get("system_language"))}


@app.post("/api/language")
def set_language(body: LanguageIn) -> dict:
    language = _normalize_language(body.language)
    accounting.set_setup_state({"system_language": language})
    return {"language": language}


@app.get("/api/hosted-region")
def get_hosted_region() -> dict:
    state = accounting.get_setup_state()
    region = _normalize_hosted_region(state.get("hosted_region"))
    return {
        "region": region,
        "label": _hosted_region_label(region),
        "available": _available_hosted_regions(),
    }


@app.post("/api/hosted-region")
def set_hosted_region(body: HostedRegionIn) -> dict:
    region = _normalize_hosted_region(body.region)
    state = accounting.set_setup_state({"hosted_region": region})
    return {
        "region": region,
        "label": _hosted_region_label(region),
        "available": _available_hosted_regions(),
        "state": state,
    }


@app.post("/api/hosted-region/detect")
def detect_hosted_region(body: HostedRegionDetectIn) -> dict:
    region = _detect_region_from_client(body)
    state = accounting.set_setup_state({"hosted_region": region})
    return {
        "region": region,
        "label": _hosted_region_label(region),
        "state": state,
    }


@app.get("/api/permission-scope")
def get_permission_scope() -> dict:
    state = accounting.get_setup_state()
    scope = _normalize_permission_scope(state.get("permission_scope"))
    return {
        "scope": scope,
        "label": _permission_scope_label(scope),
        "available": _available_permission_scopes(),
    }


@app.post("/api/permission-scope")
def set_permission_scope(body: PermissionScopeIn) -> dict:
    scope = _normalize_permission_scope(body.scope)
    state = accounting.set_setup_state({"permission_scope": scope})
    return {
        "scope": scope,
        "label": _permission_scope_label(scope),
        "available": _available_permission_scopes(),
        "state": state,
    }


@app.get("/api/terminal-access")
def get_terminal_access() -> dict:
    state = accounting.get_setup_state()
    access = _normalize_terminal_access(state.get("terminal_access"))
    return {
        "access": access,
        "label": _terminal_access_label(access),
        "available": _available_terminal_access(),
    }


@app.post("/api/terminal-access")
def set_terminal_access(body: TerminalAccessIn) -> dict:
    access = _normalize_terminal_access(body.access)
    state = accounting.set_setup_state({"terminal_access": access})
    return {
        "access": access,
        "label": _terminal_access_label(access),
        "available": _available_terminal_access(),
        "state": state,
    }


@app.get("/api/route")
def get_route() -> dict:
    state = accounting.get_setup_state()
    if state.get("onboarding_mode"):
        _restore_route_from_onboarding_mode(state["onboarding_mode"])
    return {
        "route": accounting.current_route(),
        "hosted_region": _normalize_hosted_region(state.get("hosted_region")),
        "available": sorted(accounting.ROUTES),
        "api_keys": accounting.list_api_keys(),
    }


@app.post("/api/route")
def set_route(body: RouteIn) -> dict:
    try:
        route = accounting.set_route(body.route)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"route": route, "available": sorted(accounting.ROUTES), "api_keys": accounting.list_api_keys()}


@app.post("/api/api-keys")
def save_api_key(body: ApiKeyIn) -> dict:
    try:
        item = accounting.set_api_key(body.provider, body.api_key)
        accounting.set_route("byo")
        accounting.set_setup_state(
            {
                "onboarding_completed": "1",
                "onboarding_mode": "own_api",
                "model": settings.model,
            }
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**item, "route": accounting.current_route()}


@app.post("/api/api-keys/validate")
def validate_api_key(body: ApiKeyValidationIn) -> dict:
    provider = body.provider.strip().lower()
    model = body.model.strip()
    api_key = body.api_key.strip()
    if provider not in accounting.PROVIDERS:
        raise HTTPException(400, f"unsupported provider: {provider}")
    if not api_key:
        raise HTTPException(400, "empty api key")
    expected_provider = accounting.provider_for_model(model)
    if expected_provider and expected_provider != provider:
        raise HTTPException(400, f"selected model uses {expected_provider}, not {provider}")
    try:
        _validate_api_key_live(model=model, api_key=api_key)
    except Exception as e:
        raise HTTPException(400, _friendly_runtime_error(str(e)))
    return {"ok": True, "provider": provider, "model": model}


@app.get("/api/api-keys")
def api_keys() -> dict:
    return {"items": accounting.list_api_keys(), "providers": sorted(accounting.PROVIDERS)}


@app.delete("/api/api-keys/{provider}")
def delete_api_key(provider: str) -> dict:
    return accounting.delete_api_key(provider)


@app.get("/api/workspace")
def get_workspace() -> dict:
    _restore_workspace_from_state()
    workspace = settings.workspace_dir.resolve()
    return {"workspace": str(workspace), "uses_default": _is_default_workspace(workspace)}


@app.get("/api/folders")
def list_folders(path: Optional[str] = None) -> dict:
    target = Path(path).expanduser().resolve() if path else _default_folder_picker_path()
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "folder path must be an existing folder")

    items: list[dict] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        raise HTTPException(400, f"folder is not readable: {e}")

    for child in children:
        if child.name.startswith(".") or not child.is_dir():
            continue
        readable = True
        try:
            next(child.iterdir(), None)
        except OSError:
            readable = False
        items.append({"name": child.name, "path": str(child.resolve()), "readable": readable})
        if len(items) >= 300:
            break

    parent = target.parent if target.parent != target else None
    return {
        "path": str(target),
        "parent": str(parent) if parent else None,
        "items": items,
        "truncated": len(items) >= 300,
    }


def _default_folder_picker_path() -> Path:
    desktop = Path.home() / "Desktop"
    return desktop.resolve() if desktop.exists() and desktop.is_dir() else Path.home().resolve()


@app.post("/api/workspace")
def set_workspace(body: WorkspaceIn) -> dict:
    target = _set_workspace_path(body.path)
    accounting.set_setup_state({"workspace_dir": str(target)})
    return {"workspace": str(target)}


@app.get("/api/billing")
def billing_state() -> dict:
    summary = accounting.usage_summary()
    account = accounting.hosted_account_summary()
    return {
        "hosted_account": account,
        "hosted_region": account.get("region", "auto"),
        "usage": summary,
        "route": accounting.current_route(),
    }


@app.get("/healthz")
def health():
    return {"ok": True, "model": settings.model, "route": accounting.current_route()}


def _safe_input_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()
    if not safe or safe in {".", ".."}:
        raise HTTPException(400, "invalid filename")
    return safe[:180]


def _available_models() -> list[str]:
    models = [
        settings.model,
        "claude-sonnet-4-5",
        "gpt-4o-mini",
        "deepseek/deepseek-chat",
        "ollama/llama3.1",
    ]
    out: list[str] = []
    for model in models:
        if model and model not in out:
            out.append(model)
    return out


def _store_onboarding_memory_candidates(body: OnboardingIn) -> list[str]:
    if not body.save_profile:
        return []
    ids: list[str] = []
    agent_name = (body.agent_name or "").strip()
    persona = _normalize_persona(body.persona)
    user_intro = (body.user_intro or "").strip()
    if agent_name:
        ids.append(
            memory.remember(
                key="Agent name",
                value=f"用户希望把这个 Agent 叫做：{agent_name}",
                tags=["onboarding", "agent_name"],
                type="preference",
                importance=3,
            )
        )
    if persona != "professional":
        ids.append(
            memory.remember(
                key="Agent persona",
                value=f"用户希望 Agent 使用的交流风格：{_persona_label(persona)}",
                tags=["onboarding", "persona"],
                type="preference",
                importance=3,
            )
        )
    if user_intro:
        ids.append(
            memory.remember(
                key="User self introduction",
                value=user_intro,
                tags=["onboarding", "user_profile"],
                type="preference",
                importance=4,
            )
        )
    return ids


def _normalize_persona(persona: Optional[str]) -> str:
    value = (persona or "professional").strip()
    return value if value in _PERSONA_LABELS else "professional"


def _normalize_language(language: Optional[str]) -> str:
    value = (language or "zh").strip().lower()
    return value if value in {"zh", "en"} else "zh"


def _normalize_hosted_region(region: Optional[str]) -> str:
    value = (region or "auto").strip().lower()
    return value if value in {"auto", "global", "cn"} else "auto"


def _detect_region_from_client(body: HostedRegionDetectIn) -> str:
    timezone = (body.timezone or "").lower()
    locale = (body.locale or "").lower()
    languages = " ".join((body.languages or [])).lower()
    text = f"{timezone} {locale} {languages}"
    if "shanghai" in timezone or "chongqing" in timezone or "urumqi" in timezone:
        return "cn"
    if "zh-cn" in text or "hans-cn" in text:
        return "cn"
    return "global"


def _available_hosted_regions() -> list[dict[str, str]]:
    return [
        {"region": "auto", "label": "自动选择区域"},
        {"region": "global", "label": "海外 Vercel"},
        {"region": "cn", "label": "国内阿里云"},
    ]


def _hosted_region_label(region: Optional[str]) -> str:
    labels = {
        "auto": "自动选择区域",
        "global": "海外 Vercel",
        "cn": "国内阿里云",
    }
    return labels.get(_normalize_hosted_region(region), labels["auto"])


def _normalize_permission_scope(scope: Optional[str]) -> str:
    value = (scope or "workspace").strip().lower()
    return value if value in {"workspace", "full_computer"} else "workspace"


def _normalize_terminal_access(access: Optional[str]) -> str:
    value = (access or "disabled").strip().lower()
    return value if value in {"disabled", "enabled"} else "disabled"


def _normalize_terminal_permission(permission: Optional[str]) -> str:
    value = (permission or "").strip().lower()
    return value if value in {"once", "always", "no"} else ""


def _available_permission_scopes() -> list[dict[str, str]]:
    return [
        {"scope": "workspace", "label": "仅授权文件夹"},
        {"scope": "full_computer", "label": "整台电脑"},
    ]


def _available_terminal_access() -> list[dict[str, str]]:
    return [
        {"access": "disabled", "label": "关闭终端命令"},
        {"access": "enabled", "label": "允许终端命令"},
    ]


def _permission_scope_label(scope: Optional[str]) -> str:
    labels = {
        "workspace": "仅授权文件夹",
        "full_computer": "整台电脑",
    }
    return labels.get(_normalize_permission_scope(scope), labels["workspace"])


def _terminal_access_label(access: Optional[str]) -> str:
    labels = {
        "disabled": "关闭终端命令",
        "enabled": "允许终端命令",
    }
    return labels.get(_normalize_terminal_access(access), labels["disabled"])


_TERMINAL_INTENT_KEYWORDS = (
    "终端",
    "命令",
    "shell",
    "terminal",
    "command",
    "执行",
    "运行",
    "打开",
    "启动",
    "跑一下",
    "跑测试",
    "打开网页",
    "打开html",
)
_COMMAND_LIKE_RE = re.compile(
    r"(^|\s)(open|npm|pnpm|yarn|pip|pytest|python3?|node|git|ls|pwd|cat|mkdir|touch|curl|brew|uvicorn|docker)\b",
    re.IGNORECASE,
)


def _should_request_terminal_permission(message: str, terminal_permission: str) -> bool:
    if terminal_permission in {"once", "always", "no"}:
        return False
    state = accounting.get_setup_state()
    if _normalize_terminal_access(state.get("terminal_access")) == "enabled":
        return False
    text = message.strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _TERMINAL_INTENT_KEYWORDS) or bool(_COMMAND_LIKE_RE.search(text))


_PERSONA_LABELS = {
    "professional": "专业简洁",
    "cool_sister": "高冷御姐",
    "warm_uncle": "知心大叔",
    "reliable_bro": "可靠小哥",
    "cheerful_girl": "元气萌妹",
}


def _persona_label(persona: str) -> str:
    return _PERSONA_LABELS.get(persona, _PERSONA_LABELS["professional"])


def _restore_route_from_onboarding_mode(mode: str) -> None:
    route_by_mode = {
        "own_api": "byo",
        "hosted_api": _hosted_api_route(),
        "local_model": "local",
    }
    route = route_by_mode.get((mode or "").strip().lower())
    if route:
        accounting.set_route(route)


def _restore_workspace_from_state(state: Optional[dict[str, str]] = None) -> None:
    data = state or accounting.get_setup_state()
    path = data.get("workspace_dir")
    if not path:
        return
    target = Path(path).expanduser().resolve()
    if target.exists() and target.is_dir():
        settings.workspace_dir = target


def _set_workspace_path(path: Optional[str]) -> Path:
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(400, "empty workspace path")
    target = Path(raw).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "workspace path must be an existing folder")
    settings.workspace_dir = target
    return target


def _is_default_workspace(path: Path) -> bool:
    default_workspace = (Path(__file__).resolve().parents[1] / "inputs").resolve()
    return path.resolve() == default_workspace


def _hosted_api_route() -> str:
    return "proxy" if settings.proxy_base_url else "local"


def _friendly_runtime_error(message: str) -> str:
    language = _normalize_language(accounting.get_setup_state().get("system_language"))
    if "PROXY_BASE_URL" in message:
        if language == "en":
            return (
                "Auctus Hosted API cloud Relay is not configured yet. "
                "For local preview, configure the platform model API key in .env, or switch to Own API."
            )
        return (
            "Auctus 托管 API 的云端 Relay 还没有配置。"
            "本地预览请先在 .env 配置平台模型 API key，或切换到“自己的 API”。"
        )
    if "requires a saved" in message:
        if language == "en":
            return "The current route is BYO Key, but no API key was found for this model provider. Save a key in Settings, or run Setup again."
        return "当前是 BYO Key 路由，但没有找到对应模型服务商的 API key。请在右侧保存 key，或重新运行首次设置。"
    if "Authentication" in message or "401" in message or "Unauthorized" in message:
        if language == "en":
            return "Model API authentication failed. Check the API key for the current model, or switch to Own API and save a valid key."
        return "模型 API 认证失败。请检查当前模型对应的 API key，或切换到“自己的 API”后重新保存一个有效 key。"
    return message


def _validate_api_key_live(*, model: str, api_key: str) -> None:
    litellm.completion(
        model=model,
        api_key=api_key,
        messages=[{"role": "user", "content": "Reply with ok."}],
        max_tokens=3,
        temperature=0,
    )


def _file_url(path: str) -> str:
    p = Path(path).resolve()
    root = settings.output_dir.resolve()
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = Path(p.name)
    return "/files/" + "/".join(rel.parts)
