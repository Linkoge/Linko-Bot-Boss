'use strict';
// A recording belongs to its originating project, conversation and mode for its entire lifetime.
const audioState={session:null};
function audioSupported(){return window.isSecureContext&&navigator.mediaDevices?.getUserMedia&&window.MediaRecorder;}
function renderAudio(){
  const session=audioState.session,phase=session?.phase;
  const recording=phase==='recording',busy=['starting','stopping','transcribing','sending'].includes(phase);
  $('audio-panel').hidden=!session;
  $('audio-status').textContent=recording?session.message:session?`${session.label} · ${session.message||({starting:'Requesting microphone…',recording:'Recording',stopping:'Finishing recording…',transcribing:'Transcribing…',sending:'Sending…'}[phase]||'Recording saved')}`:'';
  $('audio-cancel').disabled=phase==='sending';
  $('audio-download').hidden=!session?.blob;
  $('microphone').textContent=recording?'Stop':phase==='error'?'Retry':'🎙';
  $('microphone').setAttribute('aria-label',recording?'Stop recording and transcribe':phase==='error'?'Retry recording transcription':'Record microphone');
  $('microphone').title=audioSupported()?'Record microphone':!window.isSecureContext?'Microphone requires HTTPS on mobile':'Microphone recording is unavailable in this browser';
  $('microphone').disabled=busy||(!session&&(!!state.execution||state.pending.has(state.project)||submissions.has(state.project)||!state.history));
  $('send').type=session?'button':'submit';
  $('send').disabled=session?!recording||session.project!==state.project||!!state.execution||state.pending.has(state.project):!!state.execution||state.pending.has(state.project);
  for(const id of ['archive-chat','clear-chat'])if(session&&session.project===state.project)$(id).disabled=true;
}
function audioChanged(auth=false){
  const session=audioState.session;if(!session)return;
  session.send=false;session.changed=true;
  if(auth){session.authLost=true;session.controller?.abort();}
  if(session.phase==='starting'){discardAudio();return;}
  if(session.phase==='recording')finishAudio(false);
  renderAudio();
}
function releaseMicrophone(session){clearInterval(session.timer);session.stream?.getTracks().forEach(track=>track.stop());}
function discardAudio(){
  const session=audioState.session;if(!session||session.phase==='sending')return;
  audioState.session=null;session.controller?.abort();
  if(session.recorder?.state==='recording')session.recorder.stop();
  releaseMicrophone(session);renderAudio();
}
async function startAudio(){
  if(audioState.session)return;
  if(!audioSupported()){notice(!window.isSecureContext?'Open this manager over HTTPS to use the microphone on mobile.':'This browser does not support microphone recording. Try a current browser.');return;}
  if(state.execution||state.pending.has(state.project)||submissions.has(state.project)||!state.history)return;
  const session={project:state.project,conversation:state.history.conversation.id,mode:$('chat-mode').value,label:projectName(state.project),phase:'starting',chunks:[],bytes:0,send:false};
  audioState.session=session;renderAudio();
  try{
    const config=await api('/api/audio');if(audioState.session!==session)return;
    if(!config.available)throw new Error(config.reason);
    session.config=config;
    const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(type=>config.mime_types.includes(type.split(';')[0])&&MediaRecorder.isTypeSupported(type));
    if(!mime)throw new Error('This browser has no supported audio recording format.');
    const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(audioState.session!==session){stream.getTracks().forEach(track=>track.stop());return;}
    session.stream=stream;session.recorder=new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000});
    session.recorder.ondataavailable=event=>{if(event.data.size){session.chunks.push(event.data);session.bytes+=event.data.size;if(session.bytes>=config.max_bytes&&session.phase==='recording'){session.message='Size limit reached';finishAudio(false);}}};
    session.recorder.onerror=()=>{session.message='Microphone interrupted. Retry the saved recording or record again.';session.send=false;if(session.phase==='recording')finishAudio(false);};
    session.recorder.onstop=()=>{
      releaseMicrophone(session);
      if(audioState.session!==session)return;
      session.blob=new Blob(session.chunks,{type:session.recorder.mimeType});session.chunks=[];
      if(session.authLost){session.phase='error';session.message='Recording saved in this tab. Sign in, then retry.';renderAudio();return;}
      transcribeAudio(session);
    };
    session.recorder.start(250);session.phase='recording';session.message='00:00';session.started=performance.now();
    session.timer=setInterval(()=>{
      if(session.phase!=='recording')return;
      const elapsed=Math.floor((performance.now()-session.started)/1000);
      session.message=`${String(Math.floor(elapsed/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')}`;
      renderAudio();if(elapsed>=config.max_seconds)finishAudio(false);
    },250);
    for(const track of stream.getTracks())track.onended=()=>{if(session.phase==='recording')finishAudio(false);};
    renderAudio();
  }catch(error){
    releaseMicrophone(session);if(audioState.session!==session)return;
    session.phase='error';session.message=error.name==='NotAllowedError'?'Microphone permission denied. Allow it in browser settings, then retry.':error.name==='NotFoundError'?'No microphone found. Connect one, then retry.':error.message;renderAudio();
  }
}
function finishAudio(send){
  const session=audioState.session;if(session?.phase!=='recording')return;
  session.send=send&&!session.changed;session.phase='stopping';session.message='';clearInterval(session.timer);renderAudio();
  session.recorder.stop();releaseMicrophone(session);
}
function appendTranscript(session,text){
  const visible=state.project===session.project;
  const draft=visible?$('message-input').value:state.drafts.get(session.project)||'';
  const merged=draft+(draft&&!/\s$/.test(draft)?' ':'')+text;
  if(merged.length>$('message-input').maxLength)throw new Error('Draft plus transcript exceeds the text limit. Shorten the original draft and retry.');
  state.drafts.set(session.project,merged);if(visible)$('message-input').value=merged;
  return merged;
}
async function transcribeAudio(session){
  if(audioState.session!==session||session.phase==='transcribing')return;
  session.phase='transcribing';session.message='';session.authLost=false;session.controller=new AbortController();renderAudio();
  const timeout=setTimeout(()=>session.controller.abort(),480000);
  let submitting=false;
  try{
    if(!session.blob?.size)throw new Error('No audio captured. Discard and record again.');
    if(session.blob.size>session.config.max_bytes)throw new Error('Recording exceeds the upload limit. Download it or discard and record again.');
    let text=session.transcript;
    if(!text){const result=await api('/api/audio/transcribe',{method:'POST',headers:{'Content-Type':session.blob.type},body:session.blob,signal:session.controller.signal});text=result.text;}
    if(audioState.session!==session)return;
    if(typeof text!=='string'||!text.trim())throw new Error('No speech recognized. Retry or record again.');
    session.transcript=text.trim();
    const content=appendTranscript(session,session.transcript);
    // Context switches, history changes, an existing retry, and active inference suppress automatic Send.
    const send=session.send&&!session.changed&&state.project===session.project&&state.history?.conversation.id===session.conversation&&!state.execution&&!state.pending.has(session.project)&&!submissions.has(session.project);
    audioState.session=null;renderAudio();
    if(send){
      submitting=true;
      await sendSubmission({project_id:session.project,conversation_id:session.conversation,mode:session.mode,content});
    }else if(state.project===session.project){$('message-input').focus();}
    else notice(`Transcript added to ${session.label}'s draft.`);
  }catch(error){
    if(audioState.session!==session){if(submitting)notice(error.message);return;}
    session.send=false;session.phase='error';session.message=error.message+' Recording kept in this tab.';renderAudio();
  }finally{clearTimeout(timeout);}
}
$('microphone').onclick=()=>{
  const session=audioState.session;
  if(session?.phase==='recording')finishAudio(false);
  else if(session?.phase==='error'&&session.blob)transcribeAudio(session);
  else if(!session||session.phase==='error'){discardAudio();startAudio();}
};
$('send').addEventListener('click',()=>{if(audioState.session?.phase==='recording')finishAudio(true);});
$('message-input').addEventListener('input',()=>{const session=audioState.session;if(session&&session.phase!=='recording')session.send=false;});
document.addEventListener('visibilitychange',()=>{if(document.hidden)audioChanged();});
$('audio-cancel').onclick=discardAudio;
$('audio-download').onclick=()=>{
  const session=audioState.session;if(!session?.blob)return;
  const link=element('a');link.href=URL.createObjectURL(session.blob);link.download='recording.'+({'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'m4a','audio/wav':'wav'}[session.blob.type.split(';')[0]]||'audio');link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
};
window.addEventListener('beforeunload',event=>{if(audioState.session){event.preventDefault();event.returnValue='';}});
renderAudio();
