package local.lectureflow.validation;

import android.content.Context;
import java.io.*;
import java.net.*;
import java.security.*;

final class ModelFiles {
  static final String ASR =
      "https://huggingface.co/csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/resolve/68818b2313fe77bd06f6a7c5068ff3ef59d02b8a/";
  static final String MT =
      "https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/a0c709d9fac510f2c807aa3af52872340dc37a4a/";
  static final String[][] FILES = {
    {
      "qwen/conv_frontend.onnx",
      ASR + "conv_frontend.onnx",
      "d22dc4423e0940e49884e903d2ea2f7e5567c14fc1aed97e4e26d6b8f208ef9e"
    },
    {
      "qwen/encoder.int8.onnx",
      ASR + "encoder.int8.onnx",
      "60748d3e6744a57c9c91e1b17424a6c2990567e8adceb0783940c03ed98fa9d9"
    },
    {
      "qwen/decoder.int8.onnx",
      ASR + "decoder.int8.onnx",
      "4f6885be5959ae26af3089d38ee7972c5fafbeeb1cf8d5e76eab6d8b61ca5771"
    },
    {
      "qwen/tokenizer/merges.txt",
      ASR + "tokenizer/merges.txt",
      "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"
    },
    {
      "qwen/tokenizer/tokenizer_config.json",
      ASR + "tokenizer/tokenizer_config.json",
      "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c"
    },
    {
      "qwen/tokenizer/vocab.json",
      ASR + "tokenizer/vocab.json",
      "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
    },
    {
      "Hy-MT2-Q4.gguf",
      MT + "Hy-MT2-1.8B-Q4_K_M.gguf",
      "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699"
    }
  };
  static volatile boolean downloading;
  static volatile boolean cancel;
  static volatile String progress = "首次使用需要准备本地模型（约 2 GB），完成后可断网使用。";

  static File root(Context c) {
    return new File(c.getNoBackupFilesDir(), "models");
  }

  static boolean ready(Context c) {
    if (!new File(root(c), "verified").exists()) return false;
    for (String[] f : FILES) if (!new File(root(c), f[0]).isFile()) return false;
    return true;
  }

  static synchronized void prepare(Context context) {
    if (downloading) return;
    downloading = true;
    cancel = false;
    Context app = context.getApplicationContext();
    new Thread(
            () -> {
              try {
                for (String[] item : FILES) {
                  if (cancel) throw new InterruptedIOException("准备已暂停，可继续");
                  fetch(root(app), item);
                }
                try (FileOutputStream output =
                    new FileOutputStream(new File(root(app), "verified"))) {
                  output.write("v1".getBytes());
                }
                progress = "本地模型已校验，可断网开始录音";
              } catch (Exception error) {
                progress = "模型准备未完成，可重试。" + error.getMessage();
              } finally {
                downloading = false;
              }
            },
            "model-download")
        .start();
  }

  static void fetch(File root, String[] item) throws Exception {
    File target = new File(root, item[0]);
    target.getParentFile().mkdirs();
    progress = "校验 " + item[0];
    if (target.exists() && digest(target).equals(item[2])) return;
    File partial = new File(target.getPath() + ".partial");
    if (partial.exists() && digest(partial).equals(item[2])) {
      if (target.exists() && !target.delete()) throw new IOException("无法替换损坏模型");
      if (!partial.renameTo(target)) throw new IOException("无法保存模型");
      return;
    }
    long offset = partial.length();
    HttpURLConnection connection = (HttpURLConnection) new URL(item[1]).openConnection();
    connection.setConnectTimeout(30000);
    connection.setReadTimeout(30000);
    if (offset > 0) connection.setRequestProperty("Range", "bytes=" + offset + "-");
    int status = connection.getResponseCode();
    boolean append = status == 206 && offset > 0;
    if (status == 416) {
      connection.disconnect();
      if (!partial.delete()) throw new IOException("无法重置下载片段");
      throw new IOException("下载片段无效，点击继续重新下载");
    }
    if (status != 200 && status != 206) {
      connection.disconnect();
      throw new IOException("下载 HTTP " + status);
    }
    if (append
        && (connection.getHeaderField("Content-Range") == null
            || !connection.getHeaderField("Content-Range").startsWith("bytes " + offset + "-"))) {
      connection.disconnect();
      throw new IOException("下载区间校验失败");
    }
    long done = append ? offset : 0;
    try (InputStream input = connection.getInputStream();
        FileOutputStream output = new FileOutputStream(partial, append)) {
      byte[] buffer = new byte[65536];
      int count;
      while ((count = input.read(buffer)) >= 0) {
        if (cancel) throw new InterruptedIOException("准备已暂停，可继续");
        output.write(buffer, 0, count);
        done += count;
        progress = "下载 " + item[0] + " · " + done / 1048576 + " MB";
      }
      output.getFD().sync();
    } finally {
      connection.disconnect();
    }
    progress = "校验 " + item[0];
    if (!digest(partial).equals(item[2])) {
      partial.delete();
      throw new IOException("模型摘要不一致，请重试");
    }
    if (target.exists() && !target.delete()) throw new IOException("无法替换损坏模型");
    if (!partial.renameTo(target)) throw new IOException("无法保存模型");
  }

  static String digest(File file) throws Exception {
    MessageDigest hash = MessageDigest.getInstance("SHA-256");
    try (InputStream input = new FileInputStream(file)) {
      byte[] data = new byte[65536];
      int n;
      while ((n = input.read(data)) >= 0) {
        if (cancel) throw new InterruptedIOException("准备已暂停");
        hash.update(data, 0, n);
      }
    }
    StringBuilder value = new StringBuilder();
    for (byte b : hash.digest()) value.append(String.format("%02x", b & 255));
    return value.toString();
  }
}
