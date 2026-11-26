// 참고 시세 불러오기: 로딩·오류·권한 없음·연결 필요 상태를 각각 표시한다.
(function () {
  const box = document.querySelector("[data-quote-code]");
  if (!box) return;
  const btn = box.querySelector("button");
  const body = box.querySelector(".quote-body");
  btn.addEventListener("click", async () => {
    box.dataset.state = "loading";
    body.textContent = "";
    try {
      const res = await fetch(`/api/quote/${encodeURIComponent(box.dataset.quoteCode)}`, { credentials: "same-origin" });
      if (res.status === 401) { box.dataset.state = "denied"; body.textContent = "로그인이 만료되었습니다. 다시 로그인하세요."; return; }
      if (!res.ok) { box.dataset.state = "error"; body.textContent = `오류 (HTTP ${res.status})`; return; }
      const d = await res.json();
      box.dataset.state = d.status;
      if (d.status !== "ok") {
        body.textContent = d.status === "not_configured" ? `연결 필요 — ${d.detail}` : `${d.status} — ${d.detail}`;
        return;
      }
      const lines = [
        `참고 가격: ${d.price !== null ? d.price.toLocaleString("ko-KR") + "원" : "확인 안 됨"} (B등급, 공식 평가 아님)`,
        `원자료 기준시각: ${d.source_time ?? "없음"} · 수신: ${d.received_at}`,
        `장 상태 ${d.market_status ?? "?"} · 세션 ${d.session_type ?? "?"} · 거래소 ${d.exchange ?? "?"}`,
        d.eligible_for_paper_fill ? "모의체결 근거 적격" : `모의체결 근거 부적격: ${d.ineligible_reason}`,
      ];
      body.textContent = lines.join("\n");
    } catch (e) {
      box.dataset.state = "error";
      body.textContent = "네트워크 오류로 불러오지 못했습니다.";
    }
  });
})();
