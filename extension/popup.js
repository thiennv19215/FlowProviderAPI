const statusEl=document.querySelector('#status'),serverEl=document.querySelector('#server'),tokenEl=document.querySelector('#token'),accountEl=document.querySelector('#account'),errorEl=document.querySelector('#error');
const send=(message)=>chrome.runtime.sendMessage(message);
async function refresh(){const s=await send({type:'FLOW_PROVIDER_GET_STATE'});serverEl.value=s.serverUrl||'';tokenEl.value='';tokenEl.placeholder=s.gatewayTokenConfigured?'Configured — leave blank to keep':'Optional for local; required in production';statusEl.textContent=s.connected?'Connected':'Disconnected';accountEl.textContent=s.account?.email?`${s.account.email}${Number.isFinite(s.account.credits)?` · ${s.account.credits} credits`:''}`:'Open Google Flow and sign in.';}
document.querySelector('#save').onclick=async()=>{errorEl.textContent='';const message={type:'FLOW_PROVIDER_SET_SERVER',serverUrl:serverEl.value};if(tokenEl.value.trim())message.gatewayToken=tokenEl.value.trim();const r=await send(message);if(!r.ok)errorEl.textContent=r.error||'Cannot save server';tokenEl.value='';setTimeout(refresh,300)};
document.querySelector('#flow').onclick=()=>send({type:'FLOW_PROVIDER_OPEN_FLOW'});
refresh();
