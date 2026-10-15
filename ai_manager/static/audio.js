'use strict';
// A recording belongs to its originating project and conversation for its entire lifetime.
const audioState={session:null};
// Clone trusted repository markup only; uploaded SVG files never enter the DOM.
const microphoneIcon=$('microphone').firstElementChild.cloneNode(true);
function audioSupported(){return window.isSecureContext&&navigator.mediaDevices?.getUserMedia&&window.MediaRecorder;}
function renderAudio(){
  const session=audioState.session,phase=session?.phase;
  const recording=phase==='recording',busy=['starting','stopping','transcribing','sending'].includes(phase);
  $('audio-panel').hidden=!session;
  $('audio-status').textContent=recording?`${session.label} · ${session.muted?'Microphone paused; audio may be missing':'Recording'} · ${session.message}${session.background?' · Background capture may pause':''} · ${session.wakeLock?'Screen kept awake':'Screen may sleep'}`:session?`${session.label} · ${session.message||({starting:'Starting microphone…',recording:'Recording',stopping:'Finishing recording…',transcribing:'Transcribing…',sending:'Sending…'}[phase]||'Recording saved')}`:'';
  $('audio-stop').hidden=!recording;
  $('audio-retry').hidden=phase!=='error'||!session?.blob;
  $('audio-cancel').disabled=phase==='sending';
  $('audio-download').hidden=!session?.blob;
  if(recording||phase==='error')$('microphone').textContent=recording?'■':'Retry';
  else $('microphone').replaceChildren(microphoneIcon.cloneNode(true));
  $('microphone').setAttribute('aria-label',recording?'Stop recording and transcribe':phase==='error'?'Retry recording transcription':'Record microphone');
  $('microphone').title=audioSupported()?'Record microphone':!window.isSecureContext?'Microphone requires HTTPS on mobile':'Microphone recording is unavailable in this browser';
  $('microphone').disabled=visibilityPending.size>0||audioState.loading||busy||(!session&&(state.pending.has(state.project)||submissions.has(state.project)||!state.history));
  $('audio-upload').hidden=!recording;$('audio-upload').disabled=!recording||!session.config;
  $('send').type=session?'button':'submit';
  $('send').disabled=session?!recording||session.project!==state.project||state.pending.has(state.project)||submissions.has(state.project):state.pending.has(state.project);
  if(typeof attachmentState!=='undefined'&&attachmentState.uploads.has(state.project))$('send').disabled=true;
  if(typeof updateComposer==='function')updateComposer();
  renderProjectAvailability();
  for(const id of ['archive-chat','clear-chat','restore-trash','empty-trash'])if(session&&session.project===state.project)$(id).disabled=true;
  if(!session&&state.history)updateChatTrashAvailability();
}
function audioChanged(auth=false){
  const session=audioState.session;if(!session)return;
  session.send=false;session.changed=true;
  if(auth){session.authLost=true;session.controller?.abort();}
  if(auth){
    if(session.phase==='starting'){discardAudio();return;}
    if(session.phase==='recording')finishAudio(false);
  }
  renderAudio();
}
async function requestAudioWakeLock(session){
  if(audioState.session!==session||session.phase!=='recording'||document.hidden||session.wakePending||session.wakeLock||!navigator.wakeLock?.request)return;
  session.wakePending=true;
  try{
    const lock=await navigator.wakeLock.request('screen');
    if(audioState.session!==session||session.phase!=='recording'||document.hidden){await lock.release();return;}
    session.wakeLock=lock;
    lock.addEventListener('release',()=>{if(session.wakeLock===lock)session.wakeLock=null;if(audioState.session===session)renderAudio();},{once:true});
  }catch{/* Denial and unsupported power policies must never stop microphone capture. */}
  finally{session.wakePending=false;if(audioState.session===session)renderAudio();}
}
function releaseAudioWakeLock(session){const lock=session.wakeLock;session.wakeLock=null;if(lock)Promise.resolve(lock.release()).catch(()=>{});}
function releaseMicrophone(session){
  clearTimeout(session.startTimer);clearInterval(session.timer);releaseAudioWakeLock(session);
  session.stream?.getTracks().forEach(track=>{track.onended=null;track.onmute=null;track.onunmute=null;track.stop();});
}
function discardAudio(){
  const session=audioState.session;if(!session||session.phase==='sending')return;
  audioState.session=null;clearAudioCheckpoint();session.controller?.abort();
  if(session.recorder&&session.recorder.state!=='inactive')session.recorder.stop();
  releaseMicrophone(session);renderAudio();
}
function failAudioStartup(session,error){
  if(audioState.session!==session||session.startFailed)return;
  session.startFailed=true;session.interrupted=true;session.send=false;
  session.message=error.name==='NotAllowedError'?'Microphone permission denied. Allow it in browser settings, then retry.':error.name==='NotFoundError'?'No microphone found. Connect one, then retry.':error.message;
  session.phase='error';
  if(session.recorder&&session.recorder.state!=='inactive')session.recorder.stop();
  releaseMicrophone(session);renderAudio();
}
async function startAudio(){
  if(audioState.session||audioState.loading)return;
  if(!audioSupported()){notice(!window.isSecureContext?'Open this manager over HTTPS to use the microphone on mobile.':'This browser does not support microphone recording. Try a current browser.');return;}
  if(state.pending.has(state.project)||submissions.has(state.project)||!state.history||document.hidden||state.view!=='chat')return;
  const session={id:crypto.randomUUID(),project:state.project,conversation:state.history.conversation.id,label:projectName(state.project),phase:'starting',chunks:[],bytes:0,send:false};
  audioState.session=session;renderAudio();
  try{
    const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
    if(!mime)throw new Error('This browser has no supported audio recording format.');
    // Request permission in the click gesture. Configuration is metadata, never a capture prerequisite.
    // Start the recorder as soon as the device arrives so no opening samples wait on the network.
    const permission=navigator.mediaDevices.getUserMedia({audio:true});
    session.configReady=api('/api/audio').then(config=>{
      if(!config.available)throw new Error(config.reason);
      session.config=config;if(audioState.session===session){checkpointAudio(session,true);renderAudio();}return config;
    });
    session.configReady.catch(error=>failAudioStartup(session,error));
    session.startTimer=setTimeout(()=>failAudioStartup(session,new Error('Microphone did not start. Discard or retry when ready.')),30000);
    const stream=await permission;
    if(audioState.session!==session||session.startFailed){stream.getTracks().forEach(track=>track.stop());return;}
    session.stream=stream;session.recorder=new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000});
    session.recorder.ondataavailable=event=>{if(audioState.session!==session)return;if(event.data.size){session.chunks.push(event.data);session.bytes+=event.data.size;if(Date.now()-(session.checkpointAt||0)>2000){session.checkpointAt=Date.now();checkpointAudio(session);}if(session.config&&session.bytes>=session.config.max_bytes&&session.phase==='recording'){session.message='Size limit reached';finishAudio(false);}}};
    session.recorder.onerror=()=>{if(session.phase==='starting')failAudioStartup(session,new Error('Microphone failed to start'));else interruptAudio(session,'Microphone interrupted');};
    session.recorder.onstop=()=>{
      if(session.finalized)return;session.finalized=true;
      releaseMicrophone(session);
      if(audioState.session!==session)return;
      if(['starting','recording'].includes(session.phase)){session.interrupted=true;session.send=false;session.message='Capture stopped unexpectedly';}
      session.blob=new Blob(session.chunks,{type:session.recorder.mimeType});session.chunks=[];checkpointAudio(session,true);
      if(session.authLost){session.phase='error';session.message='Recording saved in this tab. Sign in, then retry.';renderAudio();return;}
      if(session.interrupted){session.phase='error';session.message=(session.interruptionReason||session.message||'Capture interrupted')+'. Captured audio kept; Retry transcribes it. Missing audio cannot be recovered.';renderAudio();return;}
      transcribeAudio(session);
    };
    session.recorder.onstart=()=>{
      if(audioState.session!==session||session.startFailed||session.phase!=='starting'){if(session.recorder.state!=='inactive')session.recorder.stop();releaseMicrophone(session);return;}
      clearTimeout(session.startTimer);session.phase='recording';session.message='00:00';session.started=performance.now();session.background=document.hidden;requestAudioWakeLock(session);
      session.timer=setInterval(()=>{
        if(audioState.session!==session||session.phase!=='recording')return;
        if(session.recorder.state!=='recording'||session.stream.getAudioTracks().some(track=>track.readyState==='ended')){interruptAudio(session,'Microphone capture stopped');return;}
        const elapsed=Math.floor((performance.now()-session.started)/1000);
        session.message=`${String(Math.floor(elapsed/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')}`;
        renderAudio();if(session.config&&elapsed>=session.config.max_seconds)finishAudio(false);
      },250);
      renderAudio();
    };
    for(const track of stream.getTracks()){track.onended=()=>{if(session.phase==='starting')failAudioStartup(session,new Error('Microphone ended before capture started'));else interruptAudio(session,'Microphone ended');};track.onmute=()=>{if(audioState.session!==session)return;session.muted=true;session.interruptionReason='Microphone was paused; audio may be missing';session.interrupted=true;session.send=false;renderAudio();};track.onunmute=()=>{if(audioState.session===session){session.muted=false;renderAudio();}};}
    session.recorder.start(250);
  }catch(error){failAudioStartup(session,error);}
}
function interruptAudio(session,message){
  if(audioState.session!==session||session.phase!=='recording')return;
  session.interrupted=true;session.message=message;session.send=false;finishAudio(false);
}
function finishAudio(send){
  const session=audioState.session;if(session?.phase!=='recording')return;
  session.send=send&&!session.changed;session.phase='stopping';if(!session.interrupted)session.message='';clearInterval(session.timer);renderAudio();
  // Native stop follows the final dataavailable event; never finalize ahead of it.
  if(session.recorder.state!=='inactive')session.recorder.stop();releaseMicrophone(session);
}
function appendTranscript(session,text){
  const visible=state.project===session.project;
  const draft=visible?$('message-input').value:state.drafts.get(session.project)||'';
  if(session.appended||appliedTranscripts.has(session.id))return draft;
  if(visible&&state.history?.conversation.id!==session.conversation)throw new Error('Chat changed. Download the recording or discard it; transcript will not enter a new chat.');
  const merged=draft+(draft&&!/\n$/.test(draft)?'\n\n':'')+text;
  if(merged.length>$('message-input').maxLength)throw new Error('Draft plus transcript exceeds the text limit. Shorten the original draft and retry.');
  state.drafts.set(session.project,merged);appliedTranscripts.add(session.id);saveDrafts();if(visible)$('message-input').value=merged;
  session.appended=true;session.content=merged;return merged;
}
async function transcribeAudio(session){
  if(audioState.session!==session||session.phase==='transcribing')return;
  session.phase='transcribing';session.message='';session.authLost=false;session.controller=new AbortController();renderAudio();
  const timeout=setTimeout(()=>session.controller.abort(),480000);
  let submitting=false;
  try{
    if(session.configReady)session.config=await session.configReady;
    if(audioState.session!==session)return;
    if(!session.blob?.size)throw new Error('No audio captured. Discard and record again.');
    if(session.blob.size>session.config.max_bytes)throw new Error('Recording exceeds the upload limit. Download it or discard and record again.');
    let text=session.transcript;
    if(!text){const result=await api('/api/audio/transcribe',{method:'POST',headers:{'Content-Type':session.blob.type||'application/octet-stream',...(session.filename?{'X-Audio-Filename':encodeURIComponent(session.filename)}:{})},body:session.blob,signal:session.controller.signal});text=result.text;}
    if(audioState.session!==session)return;
    if(typeof text!=='string'||!text.trim())throw new Error('No speech recognized. Retry or record again.');
    session.transcript=text.trim();checkpointAudio(session,true);
    const query=new URLSearchParams();if(session.project)query.set('project_id',session.project);
    const origin=await api('/api/history?'+query);
    if(audioState.session!==session)return;
    if(origin.conversation.id!==session.conversation)throw new Error('Original chat changed. Download captured audio; transcript will not enter a replacement chat.');
    const content=appendTranscript(session,session.transcript);
    // Context switches, history changes, an existing retry, suppress automatic Send.
    const send=session.send&&!session.changed&&state.project===session.project&&state.history?.conversation.id===session.conversation&&!state.pending.has(session.project)&&!submissions.has(session.project);
    audioState.session=null;clearAudioCheckpoint();renderAudio();
    if(send){
      submitting=true;
      await sendSubmission({project_id:session.project,conversation_id:session.conversation,content});
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
document.addEventListener('visibilitychange',()=>{
  const session=audioState.session;if(!session)return;
  session.background=document.hidden;
  if(document.hidden){session.send=false;releaseAudioWakeLock(session);}
  else if(session.phase==='recording'){
    if(session.recorder.state!=='recording'||session.stream.getAudioTracks().some(track=>track.readyState==='ended'))interruptAudio(session,'Microphone capture stopped');
    else requestAudioWakeLock(session);
  }
  renderAudio();
});
document.addEventListener('freeze',()=>{const session=audioState.session;if(session?.phase==='starting')discardAudio();else if(session)interruptAudio(session,'Browser froze capture');});
window.addEventListener('pagehide',()=>{const session=audioState.session;if(session?.phase==='starting')discardAudio();else if(session)interruptAudio(session,'Page left; capture interrupted');});
$('audio-stop').onclick=()=>finishAudio(false);
$('audio-retry').onclick=()=>{const session=audioState.session;if(session?.phase==='error'&&session.blob)transcribeAudio(session);};
$('audio-cancel').onclick=discardAudio;
$('audio-download').onclick=()=>{
  const session=audioState.session;if(!session?.blob)return;
  const link=element('a');link.href=URL.createObjectURL(session.blob);link.download=session.filename||'recording.'+({'audio/webm':'webm','audio/ogg':'ogg','audio/mp4':'m4a','audio/wav':'wav'}[session.blob.type.split(';')[0]]||'audio');link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
};
window.addEventListener('beforeunload',event=>{if(audioState.session){event.preventDefault();event.returnValue='';}});
renderAudio();

// IndexedDB stores only this browser's unfinished recording. Serial writes prevent
// a late checkpoint from resurrecting a discarded or transcribed session.
const audioTabKey=sessionStorage.getItem('linko-audio-tab-key')||crypto.randomUUID();sessionStorage.setItem('linko-audio-tab-key',audioTabKey);
let audioStorageQueue=Promise.resolve(),audioStorageWarned=false;
const audioStorage=new Promise((resolve,reject)=>{
  const request=indexedDB.open('linko-audio-recovery-v1',1);
  request.onupgradeneeded=()=>request.result.createObjectStore('recordings');
  request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
});
function audioStorageOperation(mode,run){
  const next=audioStorageQueue.then(async()=>{const db=await audioStorage;return new Promise((resolve,reject)=>{const tx=db.transaction('recordings',mode),request=run(tx.objectStore('recordings'));let value;request.onsuccess=()=>{value=request.result;};tx.oncomplete=()=>resolve(value);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});});
  audioStorageQueue=next.catch(()=>{if(!audioStorageWarned){audioStorageWarned=true;notice('Recording recovery storage unavailable. Keep this tab open; download captured audio before leaving.');}});return audioStorageQueue;
}
function checkpointAudio(session,force=false){
  if(session.checkpointPending&&!force)return session.checkpointPending;
  if(audioState.session!==session||!session.config)return Promise.resolve();
  const blob=session.blob||new Blob(session.chunks,{type:session.recorder?.mimeType});
  if(!blob.size)return Promise.resolve();
  const pending=audioStorageOperation('readwrite',store=>store.put({id:session.id,project:session.project,conversation:session.conversation,label:session.label,config:session.config,blob,filename:session.filename,transcript:session.transcript||null},audioTabKey));
  session.checkpointPending=pending;pending.finally(()=>{if(session.checkpointPending===pending)session.checkpointPending=null;});return pending;
}
function clearAudioCheckpoint(){return audioStorageOperation('readwrite',store=>store.delete(audioTabKey));}
audioState.loading=true;renderAudio();
audioStorageOperation('readonly',store=>store.get(audioTabKey)).then(saved=>{
  if(saved&&appliedTranscripts.has(saved.id)){clearAudioCheckpoint();saved=null;}
  if(saved&&!audioState.session){audioState.session={...saved,phase:'error',send:false,changed:true,chunks:[],message:'Interrupted recording recovered. Retry transcribes captured audio; missing audio cannot be recovered.'};}
  audioState.loading=false;renderAudio();
});

// Picker activation does not end capture. Only a nonempty selection replaces the
// exact session that opened it; context changes cannot discard a newer recording.
let audioPicker=null;
function restoreAudioPickerFocus(picker){restoreAttachmentFocus(picker,[$('audio-upload'),$('audio-file-input')]);}
$('audio-upload').onclick=()=>{
  const session=audioState.session;if(session?.phase!=='recording')return;
  const draft=$('message-input');
  audioPicker={session,project:session.project,conversation:session.conversation,selection:state.selection,focused:document.activeElement===draft,value:draft.value,start:draft.selectionStart,end:draft.selectionEnd,direction:draft.selectionDirection};
  $('audio-file-input').value='';$('audio-file-input').click();
};
$('audio-file-input').addEventListener('cancel',()=>{const picker=audioPicker;audioPicker=null;restoreAudioPickerFocus(picker);});
function audioPickerReturned(){
  const picker=audioPicker;
  if(picker&&!document.hidden)requestAnimationFrame(()=>{if(audioPicker===picker)restoreAudioPickerFocus(picker);});
}
window.addEventListener('focus',audioPickerReturned);
document.addEventListener('visibilitychange',audioPickerReturned);
$('audio-file-input').onchange=()=>{
  const picker=audioPicker,file=$('audio-file-input').files[0];audioPicker=null;$('audio-file-input').value='';restoreAudioPickerFocus(picker);
  if(!file||!picker)return;
  const previous=picker.session;
  if(audioState.session!==previous||previous.phase==='sending'||picker.selection!==state.selection||picker.project!==state.project||picker.conversation!==state.history?.conversation.id||state.view!=='chat'){notice('Recording or chat changed. Selected audio was not uploaded.');return;}
  // Invalidate before stop: queued final chunks, checkpoints and transcript replies
  // from the microphone are fenced by session identity, including synchronous stop.
  discardAudio();
  if(!file.size||file.size>previous.config.max_bytes){notice('Audio file must contain data and be at most 32 MiB.');return;}
  const session={id:crypto.randomUUID(),project:previous.project,conversation:previous.conversation,label:previous.label,config:previous.config,blob:file,filename:file.name,chunks:[],phase:'stopping',send:false,changed:true};
  audioState.session=session;transcribeAudio(session);
};
