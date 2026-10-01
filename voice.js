(() => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mediaStatus = document.getElementById("media-status");
  let activeRecognition = null;
  let activeCard = null;
  let recordingStartedAt = 0;
  let timerHandle = 0;
  let cameraStream = null;

  function setStatus(message, isError = false) {
    if (!mediaStatus) return;
    mediaStatus.textContent = message;
    mediaStatus.classList.toggle("voice-error", isError);
  }

  function formatTime(seconds) {
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function updateTimer(card) {
    const elapsed = Math.floor((Date.now() - recordingStartedAt) / 1000);
    card.querySelector("[data-recording-timer]").textContent = formatTime(elapsed);
  }

  function setRecordingState(card, recording, message) {
    card.classList.toggle("is-recording", recording);
    card.querySelector("[data-start-recording]").disabled = recording;
    card.querySelector("[data-stop-recording]").disabled = !recording;
    card.querySelector("[data-recording-indicator]").lastChild.textContent = ` ${message}`;
    if (!recording) {
      window.clearInterval(timerHandle);
      timerHandle = 0;
    }
  }

  function stopRecording(message = "Recording stopped.") {
    if (!activeRecognition) return;
    const recognition = activeRecognition;
    const card = activeCard;
    activeRecognition = null;
    activeCard = null;
    try {
      recognition.stop();
    } catch (error) {
      if (error.name !== "InvalidStateError") setStatus("Recording could not be stopped. You can continue typing.", true);
    }
    if (card) setRecordingState(card, false, message);
  }

  async function startRecording(card) {
    if (!SpeechRecognition) {
      setStatus("Speech recognition is unavailable in this browser. Type your answer below instead.");
      card.querySelector("[data-transcript]").textContent = "Voice transcription is not available here. You can still type your answer below.";
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("Microphone access is unavailable. Type your answer below instead.", true);
      return;
    }
    if (activeRecognition) stopRecording("Recording stopped.");
    let permissionStream;
    try {
      permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      permissionStream.getTracks().forEach(track => track.stop());
    } catch (error) {
      const message = error.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow access in your browser settings, or type your answer below."
        : error.name === "NotFoundError"
          ? "No microphone was found. You can still type your answer below."
          : "Microphone could not be opened. You can still type your answer below.";
      setStatus(message, true);
      card.querySelector("[data-transcript]").textContent = message;
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = document.documentElement.lang || navigator.language || "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    activeRecognition = recognition;
    activeCard = card;
    card.dataset.finalTranscript = "";
    const transcript = card.querySelector("[data-transcript]");
    transcript.textContent = "Listening…";

    recognition.onstart = () => {
      recordingStartedAt = Date.now();
      setRecordingState(card, true, "Recording");
      updateTimer(card);
      timerHandle = window.setInterval(() => updateTimer(card), 1000);
      setStatus("Recording in progress. Speech recognition is provided by your browser.");
    };
    recognition.onresult = event => {
      let interim = "";
      let finalized = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const phrase = event.results[index][0].transcript;
        if (event.results[index].isFinal) finalized += phrase;
        else interim += phrase;
      }
      if (finalized) {
        const answer = card.querySelector("textarea");
        const separator = answer.value && !/\s$/.test(answer.value) ? " " : "";
        answer.value += `${separator}${finalized.trim()}`;
        answer.dispatchEvent(new Event("input", { bubbles: true }));
        card.dataset.finalTranscript += `${card.dataset.finalTranscript ? " " : ""}${finalized.trim()}`;
      }
      transcript.textContent = [card.dataset.finalTranscript, interim ? `… ${interim}` : ""].filter(Boolean).join(" ");
    };
    recognition.onerror = event => {
      const message = event.error === "not-allowed" || event.error === "service-not-allowed"
        ? "Speech access was denied. Type your answer below instead."
        : event.error === "no-speech"
          ? "No speech was detected. Try again or type your answer below."
          : "Voice recognition stopped unexpectedly. Your written answer is still available.";
      setStatus(message, event.error !== "no-speech");
      transcript.textContent = message;
      if (activeRecognition === recognition) stopRecording("Recording stopped");
    };
    recognition.onend = () => {
      if (activeRecognition === recognition) {
        activeRecognition = null;
        activeCard = null;
        setRecordingState(card, false, "Ready");
      }
    };
    try {
      recognition.start();
    } catch (error) {
      activeRecognition = null;
      activeCard = null;
      setRecordingState(card, false, "Ready");
      setStatus("Voice recognition could not start. Type your answer below instead.", true);
    }
  }

  document.querySelectorAll("[data-interview-question]").forEach(card => {
    card.querySelector("[data-read-question]").addEventListener("click", () => {
      if (!("speechSynthesis" in window)) {
        setStatus("Text-to-speech is unavailable in this browser. Read the question on screen.");
        return;
      }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(card.querySelector("[data-question-text]").textContent);
      utterance.lang = document.documentElement.lang || navigator.language || "en-US";
      window.speechSynthesis.speak(utterance);
      setStatus("Reading the question aloud.");
    });
    card.querySelector("[data-start-recording]").addEventListener("click", () => startRecording(card));
    card.querySelector("[data-stop-recording]").addEventListener("click", () => stopRecording("Recording stopped"));
  });

  const cameraToggle = document.getElementById("camera-toggle");
  const video = document.getElementById("camera-preview");
  if (cameraToggle && video) {
    cameraToggle.addEventListener("click", async () => {
      if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
        video.srcObject = null;
        video.hidden = true;
        cameraToggle.textContent = "Enable camera preview";
        setStatus("Camera preview stopped. Your written answers remain available.");
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatus("Camera access is unavailable in this browser.", true);
        return;
      }
      try {
        cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
        video.srcObject = cameraStream;
        video.hidden = false;
        cameraToggle.textContent = "Turn camera off";
        setStatus("Camera preview is on. No video is recorded or uploaded.");
      } catch (error) {
        setStatus(
          error.name === "NotAllowedError"
            ? "Camera permission was denied. You can continue with written answers."
            : "Camera preview could not start. You can continue with written answers.",
          true
        );
      }
    });
  }

  window.addEventListener("beforeunload", () => {
    if (activeRecognition) stopRecording("Recording stopped");
    if (cameraStream) cameraStream.getTracks().forEach(track => track.stop());
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  });
})();
