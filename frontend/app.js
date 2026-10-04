let API_URL=localStorage.getItem("YT_API_URL")||"";
let API_KEY=localStorage.getItem("YT_API_KEY")||"";
let CLIENT_ID=localStorage.getItem("YT_CLIENT_ID")||"";
let REDIRECT_URL=localStorage.getItem("YT_REDIRECT_URL")||"";
let connected=false,auto=false;
let queue=JSON.parse(localStorage.getItem("yt_queue")||"[]");

const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const msg=(id,text,type="info")=>{const el=$(id);if(!el)return;el.textContent=text;el.className="muted message "+type;};
const setStatus=(text,ok=false)=>{$("status").textContent=text;$("status").className="badge "+(ok?"running":"stopped");};

function loadConnection(){
  if(!$("apiUrl"))return;
  $("apiUrl").value=API_URL;$("apiKey").value=API_KEY;$("clientId").value=CLIENT_ID;$("redirectUrl").value=REDIRECT_URL;
}
function readConnection(){
  API_URL=$("apiUrl").value.trim().replace(/\/$/,"");
  API_KEY=$("apiKey").value.trim();
  CLIENT_ID=$("clientId").value.trim();
  REDIRECT_URL=$("redirectUrl").value.trim();
}
function validateConnection(requireAll=true){
  readConnection();
  const missing=[];
  if(!API_URL)missing.push("백엔드 URL");
  if(requireAll&&!API_KEY)missing.push("API Key");
  if(requireAll&&!CLIENT_ID)missing.push("YouTube Client ID");
  if(requireAll&&!REDIRECT_URL)missing.push("OAuth Redirect URL");
  if(missing.length){msg("connectionStatus","설정 부족: "+missing.join(", ")+"을(를) 입력하세요.","error");setStatus("ERROR");return false;}
  try{new URL(API_URL);}catch(e){msg("connectionStatus","백엔드 URL 형식이 올바르지 않습니다. https:// 주소를 확인하세요.","error");setStatus("ERROR");return false;}
  if(requireAll){
    try{new URL(REDIRECT_URL);}catch(e){msg("connectionStatus","OAuth Redirect URL 형식이 올바르지 않습니다.","error");setStatus("ERROR");return false;}
  }
  return true;
}
function saveConnection(){
  if(!validateConnection(false))return;
  localStorage.setItem("YT_API_URL",API_URL);localStorage.setItem("YT_API_KEY",API_KEY);localStorage.setItem("YT_CLIENT_ID",CLIENT_ID);localStorage.setItem("YT_REDIRECT_URL",REDIRECT_URL);
  msg("connectionStatus","연결 정보가 저장되었습니다.","success");setStatus("READY",true);
}
async function testConnection(){
  if(!validateConnection(false))return;
  try{
    msg("connectionStatus","백엔드 연결 확인 중...");
    const r=await fetch(API_URL+"/health",{headers:API_KEY?{"X-API-Key":API_KEY}:{}});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||"HTTP "+r.status);
    msg("connectionStatus",d.oauth_configured?"백엔드 연결 성공 · OAuth 설정 감지됨":"백엔드 연결 성공 · 아직 YouTube OAuth 설정이 부족합니다.","success");
    setStatus(d.oauth_configured?"READY":"SETUP",true);
  }catch(e){msg("connectionStatus","연결 실패: "+(e.message||"서버에 접속할 수 없습니다."),"error");setStatus("ERROR");}
}
async function connectYouTube(){
  if(!validateConnection(false))return;
  try{
    msg("connectionStatus","Google 인증 주소를 생성하는 중...");
    const r=await fetch(API_URL+"/api/youtube/auth",{headers:API_KEY?{"X-API-Key":API_KEY}:{}});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||"HTTP "+r.status);
    if(!d.authorization_url)throw new Error("Google 인증 주소를 받지 못했습니다.");
    localStorage.setItem("YT_API_URL",API_URL);
    localStorage.setItem("YT_API_KEY",API_KEY);
    localStorage.setItem("YT_CLIENT_ID",CLIENT_ID);
    localStorage.setItem("YT_REDIRECT_URL",REDIRECT_URL);
    window.location.href=d.authorization_url;
  }catch(e){
    connected=false;setStatus("AUTH ERROR");
    msg("connectionStatus","YouTube 연결 실패: "+(e.message||"OAuth 설정을 확인하세요."),"error");
  }
}
async function refreshYouTubeStatus(){
  if(!API_URL)return;
  try{
    const r=await fetch(API_URL+"/api/youtube/status",{headers:API_KEY?{"X-API-Key":API_KEY}:{}});
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d.connected)return;
    connected=true;setStatus("CONNECTED",true);
    $("channelName").textContent=d.channel_name||"YouTube 채널 연결됨";
    $("channelText").textContent="YouTube 채널 정보를 정상적으로 불러왔습니다.";
    $("subs").textContent=d.subscribers??"-";
    $("views").textContent=d.views??"-";
    $("videos").textContent=d.videos??"-";
    msg("connectionStatus","YouTube 연결 성공","success");
  }catch(e){}
}
function disconnect(){
  connected=false;auto=false;setStatus("OFFLINE");
  $("channelName").textContent="채널 연결 안 됨";$("channelText").textContent="YouTube 계정을 연결하면 채널 정보를 불러옵니다.";
  $("subs").textContent="-";$("views").textContent="-";$("videos").textContent="-";$("autoState").textContent="OFF";
  msg("connectionStatus","YouTube 연결을 해제했습니다.");
  msg("uploadStatus","채널 연결 후 사용할 수 있습니다.");
}
function render(){
  $("queueCount").textContent=queue.length;
  $("queue").innerHTML=queue.length?queue.map((x,i)=>'<div class="trade"><span>'+esc(x.title||"제목 없음")+'<br><small>'+esc(x.status||"대기")+'</small></span><button class="secondary" onclick="removeItem('+i+')">삭제</button></div>').join(""):'<span class="muted">대기 중인 콘텐츠가 없습니다.</span>';
}
function saveQ(){localStorage.setItem("yt_queue",JSON.stringify(queue));render();}
function removeItem(i){queue.splice(i,1);saveQ();}

$("saveConnection").onclick=saveConnection;
$("testConnection").onclick=testConnection;
$("connect").onclick=connectYouTube;
$("disconnect").onclick=disconnect;

$("generate").onclick=()=>{
  const topic=$("topic").value.trim();
  if(!topic){msg("uploadStatus","먼저 쇼츠 주제를 입력하세요.","error");$("topic").focus();return;}
  $("title").value=$("title").value.trim()||topic+" | 60초 핵심 정리";
  $("script").value="지금부터 "+topic+"에 대해 60초 안에 핵심만 알려드리겠습니다.\n\n첫 번째 핵심을 설명합니다.\n\n두 번째로 알아둘 점입니다.\n\n마지막으로 실제 적용 방법을 정리합니다.\n\n도움이 되었다면 구독과 좋아요 부탁드립니다.";
  msg("uploadStatus","대본 초안이 생성되었습니다.","success");
};

$("draft").onclick=()=>{
  const title=$("title").value.trim(),script=$("script").value.trim();
  if(!title){msg("uploadStatus","대기열 추가 실패: 제목을 입력하세요.","error");$("title").focus();return;}
  if(!script){msg("uploadStatus","대기열 추가 실패: 대본을 입력하세요.","error");$("script").focus();return;}
  queue.push({title,status:"검수 대기",topic:$("topic").value.trim(),script,hashtags:$("hashtags").value.trim()});
  saveQ();msg("uploadStatus","콘텐츠가 대기열에 추가되었습니다.","success");
};

$("upload").onclick=async()=>{
  if(!connected){msg("uploadStatus","업로드 실패: 먼저 YouTube 연결을 완료하세요.","error");return;}
  if(!API_URL){msg("uploadStatus","업로드 실패: 백엔드 URL이 설정되지 않았습니다.","error");return;}
  const file=$("video").files[0];
  if(!file){msg("uploadStatus","업로드 실패: 영상 파일을 선택하세요.","error");return;}
  const title=$("title").value.trim();
  if(!title){msg("uploadStatus","업로드 실패: 영상 제목을 입력하세요.","error");$("title").focus();return;}
  if($("approval").checked===false){msg("uploadStatus","업로드 차단: 승인된 콘텐츠만 업로드 옵션을 체크하세요.","error");return;}
  const fd=new FormData();fd.append("video",file);fd.append("title",title);fd.append("description",$("hashtags").value.trim());fd.append("privacy",$("privacy").value);fd.append("publish_at",$("schedule").value||"");
  try{
    msg("uploadStatus","YouTube 업로드 요청 중...");
    const r=await fetch(API_URL+"/api/youtube/upload",{method:"POST",headers:API_KEY?{"X-API-Key":API_KEY}:{},body:fd});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||"HTTP "+r.status);
    if(!d.ok)throw new Error(d.message||"YouTube OAuth가 필요합니다.");
    msg("uploadStatus","YouTube 업로드 성공","success");
  }catch(e){msg("uploadStatus","업로드 실패: "+(e.message||"서버 오류"),"error");}
};

$("toggle").onclick=()=>{
  if(!auto){
    if(!connected){msg("uploadStatus","자동 업로드를 켤 수 없습니다: YouTube 연결이 필요합니다.","error");return;}
    if(!$("autoTime").value){msg("uploadStatus","자동 업로드를 켤 수 없습니다: 기본 업로드 시간을 설정하세요.","error");return;}
    if(!$("dailyLimit").value||Number($("dailyLimit").value)<1){msg("uploadStatus","자동 업로드를 켤 수 없습니다: 일일 최대 업로드 수를 확인하세요.","error");return;}
  }
  auto=!auto;$("autoState").textContent=auto?"ON":"OFF";$("toggle").textContent=auto?"자동 업로드 OFF":"자동 업로드 ON";
  msg("uploadStatus",auto?"자동 업로드가 ON으로 변경되었습니다.":"자동 업로드가 OFF로 변경되었습니다.","success");
};
$("save").onclick=()=>{
  const time=$("autoTime").value,limit=Number($("dailyLimit").value);
  if(!time){msg("uploadStatus","설정 저장 실패: 기본 업로드 시간을 입력하세요.","error");return;}
  if(!Number.isInteger(limit)||limit<1||limit>20){msg("uploadStatus","설정 저장 실패: 일일 최대 업로드는 1~20 사이여야 합니다.","error");return;}
  localStorage.setItem("yt_auto_time",time);localStorage.setItem("yt_daily_limit",String(limit));
  msg("uploadStatus","자동화 설정을 저장했습니다.","success");
};

loadConnection();render();refreshYouTubeStatus();