/* Theme: the Dark / Light preference. Loaded before the stylesheets so the first paint already uses the right theme.
   A browser preference only: kept in localStorage, no backend write. Top-level names carry the thm prefix (the console scripts share one global scope). */
'use strict';
const thmKey='stickerloom.ui.theme';
function thmGet(){try{const v=localStorage.getItem(thmKey);if(v==='light'||v==='dark')return v}catch(e){}return 'light'}
function thmApply(v){const t=v==='light'?'light':'dark';try{document.documentElement.setAttribute('data-theme',t);thmSync()}catch(e){}return t}
function thmSync(){document.querySelectorAll('[data-act=thmset]').forEach(b=>{const on=b.dataset.v===document.documentElement.getAttribute('data-theme');b.classList.toggle('on',on);b.setAttribute('aria-pressed',String(on))});const b=document.getElementById('thm-toggle');if(b){const label=thmIsDark()?'Switch to light theme':'Switch to dark theme';b.title=label;b.setAttribute('aria-label',label)}}
const thmCurrent=thmApply(thmGet());
function thmSet(v){const t=v==='light'?'light':'dark';try{localStorage.setItem(thmKey,t)}catch(e){}return thmApply(t)}
function thmIsDark(){try{return(document.documentElement.getAttribute('data-theme')||thmGet())!=='light'}catch(e){return false}}
try{window.addEventListener('storage',e=>{if(e&&(e.key===thmKey||e.key===null))thmApply(thmGet())})}catch(e){}
document.addEventListener('DOMContentLoaded',thmSync);
