import VADAudioRecorder from "@ai/vad_audio_recorder";
import { VoiceTranscription } from "@ai/editor/embedded_components/core/voice_transcription";
import { patch } from "@web/core/utils/patch";

function pickMimeType() {
    const candidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
    ];
    for (const t of candidates) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) {
            return t;
        }
    }
    return "";
}

function encodeWav(audioBuffer, targetSampleRate = 16000) {
    const numChannels = 1;
    const srcRate = audioBuffer.sampleRate;
    const srcLength = audioBuffer.length;

    let samples;
    if (srcRate === targetSampleRate) {
        samples = audioBuffer.getChannelData(0);
    } else {
        const ratio = srcRate / targetSampleRate;
        const newLength = Math.round(srcLength / ratio);
        samples = new Float32Array(newLength);
        const src = audioBuffer.getChannelData(0);
        for (let i = 0; i < newLength; i++) {
            const srcIdx = i * ratio;
            const i0 = Math.floor(srcIdx);
            const i1 = Math.min(i0 + 1, srcLength - 1);
            const frac = srcIdx - i0;
            samples[i] = src[i0] * (1 - frac) + src[i1] * frac;
        }
    }

    const dataBytes = samples.length * 2;
    const buffer = new ArrayBuffer(44 + dataBytes);
    const view = new DataView(buffer);
    const writeString = (offset, str) => {
        for (let i = 0; i < str.length; i++) {
            view.setUint8(offset + i, str.charCodeAt(i));
        }
    };
    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataBytes, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, targetSampleRate, true);
    view.setUint32(28, targetSampleRate * numChannels * 2, true);
    view.setUint16(32, numChannels * 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, dataBytes, true);
    let offset = 44;
    for (let i = 0; i < samples.length; i++, offset += 2) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
}

function isSilentBuffer(audioBuffer, rmsThreshold = 0.005) {
    const data = audioBuffer.getChannelData(0);
    if (!data.length) {
        return true;
    }
    let sumSquares = 0;
    for (let i = 0; i < data.length; i++) {
        sumSquares += data[i] * data[i];
    }
    const rms = Math.sqrt(sumSquares / data.length);
    return rms < rmsThreshold;
}

async function blobToWav(blob) {
    const arrayBuffer = await blob.arrayBuffer();
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioCtx();
    try {
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
        if (isSilentBuffer(audioBuffer)) {
            return null;
        }
        return encodeWav(audioBuffer, 16000);
    } finally {
        ctx.close?.();
    }
}

VADAudioRecorder.prototype.setupTranscriptionSession = async function (sessionInfo) {
    if (!VADAudioRecorder.audioStream) {
        console.error("[mistral] audioStream is null");
        return;
    }

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(
        VADAudioRecorder.audioStream,
        mimeType ? { mimeType } : undefined,
    );
    this._mistralMediaRecorder = recorder;
    this._mistralChunks = [];
    this._mistralMimeType = mimeType || "audio/webm";
    this._mistralItemId = `item_${Math.random().toString(36).slice(2, 18)}`;
    this._mistralLanguage = sessionInfo?.language || null;

    recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
            this._mistralChunks.push(event.data);
        }
    };

    recorder.start();
};

async function uploadAndEmit(self) {
    const chunks = self._mistralChunks || [];
    self._mistralChunks = [];
    if (!chunks.length) {
        return;
    }
    const srcBlob = new Blob(chunks, { type: self._mistralMimeType });
    const itemId = self._mistralItemId;

    let wavBlob;
    try {
        wavBlob = await blobToWav(srcBlob);
    } catch (err) {
        console.error("[mistral] webm->wav decode failed", err);
        self.onMessage?.({ type: "error", error: "audio decode failed" });
        return;
    }

    if (!wavBlob) {
        return;
    }

    let url = "/ai/transcription/audio";
    if (self._mistralLanguage) {
        url += `?language=${encodeURIComponent(self._mistralLanguage)}`;
    }

    try {
        const resp = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "audio/wav" },
            body: wavBlob,
        });
        if (!resp.ok) {
            const errBody = await resp.text();
            console.error("[mistral] transcription failed", resp.status, errBody);
            self.onMessage?.({ type: "error", error: `HTTP ${resp.status}` });
            return;
        }
        const payload = await resp.json();
        const text = (payload?.text || "").trim();
        if (!text) {
            return;
        }
        self.onMessage?.({
            type: "conversation.item.input_audio_transcription.delta",
            item_id: itemId,
            delta: text,
        });
        self.onMessage?.({
            type: "conversation.item.input_audio_transcription.completed",
            item_id: itemId,
            transcript: text,
        });
    } catch (err) {
        console.error("[mistral] transcription error", err);
        self.onMessage?.({ type: "error", error: String(err) });
    }
}

const _origStop = VADAudioRecorder.prototype.stopRecording;
VADAudioRecorder.prototype.stopRecording = async function () {
    const recorder = this._mistralMediaRecorder;
    this._mistralMediaRecorder = null;

    if (recorder && recorder.state !== "inactive") {
        await new Promise((resolve) => {
            recorder.onstop = resolve;
            recorder.stop();
        });
        await uploadAndEmit(this);
    }

    return _origStop.apply(this, arguments);
};

patch(VoiceTranscription.prototype, {
    async toggleRecording() {
        if (this.embeddedState.status === "recording") {
            this.state.isRecording = false;
            this.embeddedState.status = "summarizing";
            await this.audioRecorder.stopRecording();
            this.props.onTranscriptionUpdated("stopped", this.embeddedState.id);
            const transcriptText = this.props.getTranscriptContent(this.embeddedState.id);
            if (!transcriptText || transcriptText.trim() === "") {
                this.embeddedState.status = "idle";
                return;
            }
            await this.updateSummary();
            return;
        }
        return super.toggleRecording(...arguments);
    },
});
