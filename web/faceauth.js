/*
 * face-auth browser widget (no dependencies).
 *
 *   <script src="http://localhost:8100/faceauth.js"></script>
 *   // challenge = what your backend got from POST /v1/challenges
 *   const res = await FaceAuth.run({ serviceUrl: "http://localhost:8100", challenge });
 *   // verify -> res.token  (send it as the 'X-Face-Token' header with the critical request)
 *   // enroll -> res.enrolled === true
 *
 * The camera preview is mirrored (feels natural), the captured frames are NOT
 * (the server expects real camera images).
 */
(function (global) {
  "use strict";

  const CSS = `
  .fa-back{position:fixed;inset:0;background:rgba(10,12,16,.72);display:flex;align-items:center;
    justify-content:center;z-index:2147483000;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
  .fa-box{background:#fff;border-radius:16px;padding:20px;width:min(92vw,420px);text-align:center;
    box-shadow:0 20px 60px rgba(0,0,0,.35)}
  .fa-cam{position:relative;width:100%;aspect-ratio:3/4;border-radius:12px;overflow:hidden;background:#111}
  .fa-cam video{width:100%;height:100%;object-fit:cover;transform:scaleX(-1)}
  .fa-oval{position:absolute;inset:12% 18%;border:3px solid rgba(255,255,255,.85);border-radius:50%;
    box-shadow:0 0 0 999px rgba(0,0,0,.35)}
  .fa-step{font-size:20px;font-weight:600;margin:14px 0 4px;color:#111}
  .fa-sub{font-size:13px;color:#666;min-height:18px}
  .fa-bar{height:4px;background:#eee;border-radius:2px;margin-top:12px;overflow:hidden}
  .fa-bar>i{display:block;height:100%;width:0;background:#2563eb;transition:width .1s linear}
  .fa-btn{margin-top:14px;border:0;background:#f1f1f1;border-radius:8px;padding:8px 16px;cursor:pointer}
  .fa-ok{color:#15803d}.fa-err{color:#b91c1c}`;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function el(tag, cls, parent, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }

  async function run({ serviceUrl, challenge, holdMs = 2400, settleMs = 700, perStep = 4 }) {
    if (!document.getElementById("fa-css")) {
      const s = el("style"); s.id = "fa-css"; s.textContent = CSS; document.head.appendChild(s);
    }
    const back = el("div", "fa-back", document.body);
    const box = el("div", "fa-box", back);
    const cam = el("div", "fa-cam", box);
    const video = el("video", "", cam);
    video.playsInline = true; video.muted = true;
    el("div", "fa-oval", cam);
    const step = el("div", "fa-step", box, "Kamera açılıyor…");
    const sub = el("div", "fa-sub", box);
    const bar = el("i", "", el("div", "fa-bar", box));
    const cancel = el("button", "fa-btn", box, "Vazgeç");

    let stream, cancelled = false;
    cancel.onclick = () => { cancelled = true; };
    const cleanup = () => { if (stream) stream.getTracks().forEach((t) => t.stop()); back.remove(); };

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 960 }, height: { ideal: 1280 } }, audio: false,
      });
      video.srcObject = stream;
      await video.play();
      await sleep(500);

      const canvas = document.createElement("canvas");
      const frames = [];
      const n = challenge.actions.length;
      for (let i = 0; i < n; i++) {
        if (cancelled) throw new Error("cancelled");
        step.textContent = challenge.instructions[i];
        sub.textContent = `Adım ${i + 1}/${n}`;
        await sleep(settleMs);
        // several frames per step: the server keeps the one with the clearest movement
        const shots = [];
        const t0 = performance.now();
        let next = 0;
        while (performance.now() - t0 < holdMs) {
          if (cancelled) throw new Error("cancelled");
          const t = (performance.now() - t0) / holdMs;
          bar.style.width = `${((i + t) / n) * 100}%`;
          if (shots.length < perStep && t >= 0.25 + (0.75 * next) / perStep) {
            canvas.width = video.videoWidth; canvas.height = video.videoHeight;
            canvas.getContext("2d").drawImage(video, 0, 0);
            shots.push(canvas.toDataURL("image/jpeg", 0.85));
            next++;
          }
          await sleep(40);
        }
        frames.push(shots);
      }
      bar.style.width = "100%";
      step.textContent = "Kontrol ediliyor…"; sub.textContent = "";
      stream.getTracks().forEach((t) => t.stop());

      const r = await fetch(`${serviceUrl.replace(/\/$/, "")}/v1/challenges/${encodeURIComponent(challenge.challenge_id)}/complete`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ frames }),
      });
      const data = await r.json();
      if (!r.ok) {
        step.textContent = "Doğrulanamadı"; step.className = "fa-step fa-err";
        sub.textContent = data.message || "";
        await sleep(2200);
        const err = new Error(data.message || "face-auth failed"); err.code = data.error; throw err;
      }
      step.textContent = data.enrolled ? "Yüz kaydedildi ✓" : "Doğrulandı ✓";
      step.className = "fa-step fa-ok";
      await sleep(900);
      return data;
    } finally {
      cleanup();
    }
  }

  global.FaceAuth = { run };
})(window);
