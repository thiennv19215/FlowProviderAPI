const statusEl=document.querySelector('#status'),serverEl=document.querySelector('#server'),accountEl=document.querySelector('#account'),errorEl=document.querySelector('#error');
const send=(message)=>chrome.runtime.sendMessage(message);
async function refresh(){const s=await send({type:'FLOW_PROVIDER_GET_STATE'});serverEl.value=s.serverUrl||'';statusEl.textContent=s.connected?'Connected':'Disconnected';accountEl.textContent=s.account?.email?`${s.account.email}${Number.isFinite(s.account.credits)?` · ${s.account.credits} credits`:''}`:'Open Google Flow and sign in.';}
document.querySelector('#save').onclick=async()=>{errorEl.textContent='';const r=await send({type:'FLOW_PROVIDER_SET_SERVER',serverUrl:serverEl.value});if(!r.ok)errorEl.textContent=r.error||'Cannot save server';setTimeout(refresh,300)};
document.querySelector('#flow').onclick=()=>send({type:'FLOW_PROVIDER_OPEN_FLOW'});
refresh();
