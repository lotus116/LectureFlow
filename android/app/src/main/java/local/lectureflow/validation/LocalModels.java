package local.lectureflow.validation;

import com.k2fsa.sherpa.onnx.*;
import java.io.File;
import java.nio.charset.StandardCharsets;

final class LocalModels implements AutoCloseable, CaptionProcessor.Inference {
  static {
    System.loadLibrary("lectureflow");
  }

  private OfflineRecognizer recognizer;
  private long translator;

  LocalModels(File root) {
    OfflineQwen3AsrModelConfig qwen = new OfflineQwen3AsrModelConfig();
    File folder = new File(root, "qwen");
    qwen.setConvFrontend(new File(folder, "conv_frontend.onnx").getPath());
    qwen.setEncoder(new File(folder, "encoder.int8.onnx").getPath());
    qwen.setDecoder(new File(folder, "decoder.int8.onnx").getPath());
    qwen.setTokenizer(new File(folder, "tokenizer").getPath());
    OfflineModelConfig model = new OfflineModelConfig();
    model.setQwen3Asr(qwen);
    model.setNumThreads(4);
    OfflineRecognizerConfig config = new OfflineRecognizerConfig();
    config.setModelConfig(model);
    recognizer = new OfflineRecognizer(null, config);
    try {
      translator = loadTranslation(new File(root, "Hy-MT2-Q4.gguf").getPath());
    } catch (Throwable error) {
      recognizer.release();
      throw error;
    }
  }

  public String recognize(float[] samples) {
    OfflineStream stream = recognizer.createStream();
    try {
      stream.setOption("language", "English");
      stream.acceptWaveform(samples, 16000);
      recognizer.decode(stream);
      return recognizer.getResult(stream).getText().trim();
    } finally {
      stream.release();
    }
  }

  public String translate(String source, String preceding, String following) {
    String prompt = TranslationPolicy.prompt(source, preceding, following);
    String result =
        new String(
                translateNative(translator, prompt.getBytes(StandardCharsets.UTF_8)),
                StandardCharsets.UTF_8)
            .trim();
    return TranslationPolicy.validate(source, result);
  }

  public void close() {
    recognizer.release();
    if (translator != 0) {
      closeTranslation(translator);
      translator = 0;
    }
  }

  static native long loadTranslation(String path);

  static native byte[] translateNative(long translator, byte[] prompt);

  static native void closeTranslation(long translator);
}
