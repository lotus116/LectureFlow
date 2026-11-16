package local.lectureflow.validation;

import java.io.*;
import java.util.*;

final class CaptionProcessor {
  interface State {
    long available();

    boolean done();

    void progress(long consumed, String detail);
  }

  private final LectureStore store;

  interface Inference {
    String recognize(float[] samples);

    String translate(String text, String preceding, String following);
  }

  private final Inference models;
  private long consumed;
  private final State state;

  CaptionProcessor(LectureStore store, Inference models, State state) {
    this.store = store;
    this.models = models;
    this.state = state;
  }

  void run() throws Exception {
    long start = store.resumeSample(), cursor = start, decoded = start;
    int silence = 0;
    float[] window = new float[320000];
    int used = 0;
    String previous = "";
    try (RandomAccessFile audio =
            new RandomAccessFile(new File(store.folder, "recording.pcm"), "r");
        RandomAccessFile boundaries =
            new RandomAccessFile(new File(store.folder, "boundaries.bin"), "r")) {
      long nextBoundary = -1;
      audio.seek(cursor * 2);
      while (true) {
        long available = state.available();
        if (nextBoundary < 0 && boundaries.length() - boundaries.getFilePointer() >= 8)
          nextBoundary = boundaries.readLong();
        if (nextBoundary >= 0 && nextBoundary <= cursor) {
          if (used > 0) decode(start, cursor, Arrays.copyOf(window, used), true, previous);
          start = cursor;
          used = 0;
          silence = 0;
          previous = "";
          nextBoundary = -1;
          continue;
        }
        int count = (int) Math.min(4000, available - cursor);
        if (nextBoundary >= 0) count = (int) Math.min(count, nextBoundary - cursor);
        if (count < 4000
            && !state.done()
            && !(nextBoundary >= 0 && cursor + count == nextBoundary)) {
          Thread.sleep(50);
          continue;
        }
        if (count <= 0) {
          if (state.done()) {
            if (used > 0) decode(start, cursor, Arrays.copyOf(window, used), true, previous);
            break;
          }
          continue;
        }
        byte[] bytes = new byte[count * 2];
        audio.readFully(bytes);
        double energy = 0;
        for (int i = 0; i < count; i++) {
          short sample = (short) ((bytes[i * 2] & 255) | (bytes[i * 2 + 1] << 8));
          float value = sample / 32768f;
          window[used++] = value;
          energy += value * value;
        }
        cursor += count;
        consumed = cursor;
        double rms = Math.sqrt(energy / count);
        silence = rms < 0.008 ? silence + count : 0;
        boolean stable =
            used >= 320000
                || (used >= 32000 && silence >= 32000)
                || (state.done() && cursor >= state.available());
        if (stable || cursor - decoded >= 16000 && used >= 16000) {
          previous = decode(start, cursor, Arrays.copyOf(window, used), stable, previous);
          decoded = cursor;
        }
        if (stable) {
          start = cursor;
          used = 0;
          silence = 0;
          previous = "";
        }
        state.progress(cursor, "字幕处理中");
      }
    }
  }

  private String decode(long start, long end, float[] samples, boolean stable, String previous)
      throws Exception {
    state.progress(end, "识别英文");
    if (previous.isEmpty() && !hasSpeechEnergy(samples)) {
      if (stable) store.metadata("processed_sample", Long.toString(end));
      return "";
    }
    String text;
    try {
      text = models.recognize(samples);
    } catch (Exception error) {
      store.event("asr_failure", start, "", error.toString());
      throw error;
    }
    if (text.isEmpty()) text = previous;
    if (text.isEmpty()) {
      if (stable) store.metadata("processed_sample", Long.toString(end));
      return text;
    }
    boolean changed = !text.equals(previous);
    store.english(start, end, text, stable);
    if (changed || stable) translate(start, text, "", false);
    if (stable) {
      List<LectureStore.Caption> recent = store.recent(3);
      for (int i = 0; i < recent.size() - 1; i++) {
        LectureStore.Caption target = recent.get(i);
        if (target.english.trim().isEmpty()) continue;
        StringBuilder following = new StringBuilder();
        for (int j = i + 1; j < recent.size(); j++) {
          if (following.length() > 0) following.append('\n');
          following.append(recent.get(j).english);
        }
        translate(target.start, target.english, following.toString(), true);
      }
    }
    return text;
  }

  private static boolean hasSpeechEnergy(float[] samples) {
    // Skip digital silence and very quiet input; the untouched recording remains available.
    for (int offset = 0; offset < samples.length; offset += 4000) {
      int end = Math.min(offset + 4000, samples.length);
      double energy = 0;
      for (int i = offset; i < end; i++) energy += samples[i] * samples[i];
      if (Math.sqrt(energy / (end - offset)) >= 0.002) return true;
    }
    return false;
  }

  private void translate(long start, String text, String following, boolean revision)
      throws Exception {
    StringBuilder preceding = new StringBuilder();
    for (LectureStore.Caption row : store.recent(6))
      if (row.start < start) preceding.append(row.english).append('\n');
    state.progress(consumed, revision ? "结合后文回改中文" : "生成中文");
    for (int attempt = 0; attempt < 2; attempt++) {
      String output;
      try {
        output = models.translate(text, preceding.toString(), following);
      } catch (Exception error) {
        store.failed(
            start,
            error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
        continue;
      }
      // A storage failure must stop processing instead of masquerading as model failure.
      store.translated(start, output, revision);
      return;
    }
  }
}
