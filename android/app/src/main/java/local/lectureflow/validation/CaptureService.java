package local.lectureflow.validation;

import android.app.*;
import android.content.*;
import android.media.*;
import android.os.*;
import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

public final class CaptureService extends Service {
  static volatile CaptureService active;
  static volatile String status = "准备开始";
  static volatile File latest;
  volatile boolean recording, paused, captureDone;
  volatile long samples, consumed;
  volatile boolean working;
  private volatile String captureError = "";
  LectureStore store;
  private PowerManager.WakeLock wake;
  private volatile AudioRecord recorder;

  public IBinder onBind(Intent intent) {
    return null;
  }

  public void onCreate() {
    super.onCreate();
    NotificationManager manager = getSystemService(NotificationManager.class);
    manager.createNotificationChannel(
        new NotificationChannel("recording", "课堂录音", NotificationManager.IMPORTANCE_LOW));
  }

  public int onStartCommand(Intent intent, int flags, int id) {
    String action = intent == null ? "" : intent.getAction();
    if ("pause".equals(action)) {
      paused = !paused;
      status = paused ? "已暂停录音，继续处理已有字幕" : "正在录音";
      return START_NOT_STICKY;
    }
    if ("stop".equals(action)) {
      recording = false;
      paused = false;
      status = "录音已结束，正在补齐字幕与回改";
      return START_NOT_STICKY;
    }
    if (working) return START_NOT_STICKY;
    PendingIntent open =
        PendingIntent.getActivity(
            this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
    Notification notification =
        new Notification.Builder(this, "recording")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("LectureFlow 正在保存课堂内容")
            .setContentText("点击查看录音和字幕进度")
            .setContentIntent(open)
            .setOngoing(true)
            .build();
    startForeground(1, notification);
    active = this;
    working = true;
    try {
      File courses = new File(getNoBackupFilesDir(), "lectures");
      courses.mkdirs();
      latest =
          new File(
              courses,
              new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date())
                  + "-"
                  + UUID.randomUUID().toString().substring(0, 6));
      store = new LectureStore(latest);
      store.metadata("state", "recording");
      store.metadata("created_ms", "" + System.currentTimeMillis());
      store.metadata("device", Build.MANUFACTURER + " " + Build.MODEL);
      store.metadata("android", "" + Build.VERSION.SDK_INT);
      wake =
          ((PowerManager) getSystemService(POWER_SERVICE))
              .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LectureFlow:class");
      wake.acquire();
      new File(latest, "boundaries.bin").createNewFile();
      recording = true;
      captureDone = false;
      new Thread(this::capture, "lecture-audio-writer").start();
      new Thread(this::infer, "lecture-inference").start();
    } catch (Exception error) {
      status = "启动失败，" + error.getMessage();
      finish();
    }
    return START_NOT_STICKY;
  }

  private void capture() {
    android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO);
    try (FileOutputStream output = new FileOutputStream(new File(latest, "recording.pcm"));
        DataOutputStream boundaries =
            new DataOutputStream(new FileOutputStream(new File(latest, "boundaries.bin"), true));
        FileOutputStream captureEvents =
            new FileOutputStream(new File(latest, "capture.jsonl"), true)) {
      int min =
          AudioRecord.getMinBufferSize(
              16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
      if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
          != android.content.pm.PackageManager.PERMISSION_GRANTED) {
        throw new SecurityException("麦克风权限未授予");
      }
      recorder =
          new AudioRecord(
              MediaRecorder.AudioSource.VOICE_RECOGNITION,
              16000,
              AudioFormat.CHANNEL_IN_MONO,
              AudioFormat.ENCODING_PCM_16BIT,
              Math.max(min, 32000));
      if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new IOException("麦克风初始化失败");
      recorder.startRecording();
      byte[] bytes = new byte[8000];
      boolean wasPaused = false;
      while (recording) {
        int n = recorder.read(bytes, 0, bytes.length, AudioRecord.READ_BLOCKING);
        if (n < 0) throw new IOException("麦克风读取失败 " + n);
        if (paused) {
          if (!wasPaused) {
            boundaries.writeLong(samples);
            boundaries.flush();
            captureEvent(captureEvents, "pause", samples);
          }
          wasPaused = true;
          continue;
        }
        if (wasPaused) {
          captureEvent(captureEvents, "resume", samples);
          wasPaused = false;
        }
        output.write(bytes, 0, n);
        samples += n / 2;
        if (samples % 16000 < 4000) {
          output.getFD().sync();
          if (latest.getUsableSpace() < 100 * 1024 * 1024)
            throw new IOException("可用存储不足100MB，录音已停止");
        }
      }
      output.getFD().sync();
    } catch (Exception error) {
      captureError = "录音停止，" + error.getMessage();
      status = captureError;
    } finally {
      recording = false;
      AudioRecord current = recorder;
      if (current != null) {
        try {
          current.stop();
        } catch (Exception ignored) {
        }
        current.release();
        recorder = null;
      }
      try {
        store.metadata("recorded_samples", "" + samples);
        store.metadata("capture_error", captureError);
        store.metadata(
            "state",
            store.readMetadata("inference_error", "").isEmpty() ? "draining" : "incomplete");
      } catch (Exception ignored) {
      }
      captureDone = true;
    }
  }

  private void infer() {
    try {
      while (!new File(latest, "recording.pcm").exists() && !captureDone) Thread.sleep(20);
      status = "正在录音，加载本地模型";
      try (LocalModels models = new LocalModels(ModelFiles.root(this))) {
        CaptionProcessor processor =
            new CaptionProcessor(
                store,
                models,
                new CaptionProcessor.State() {
                  public long available() {
                    return samples;
                  }

                  public boolean done() {
                    return captureDone;
                  }

                  public void progress(long cursor, String detail) {
                    consumed = cursor;
                    status = (recording ? (paused ? "已暂停 · " : "录音中 · ") : "收尾中 · ") + detail;
                  }
                });
        processor.run();
      }
      int failures = store.failures();
      status =
          !captureError.isEmpty()
              ? captureError + "；已有内容已保存"
              : failures == 0 ? "本讲已保存，字幕和中文回改已完成" : "本讲已保存，仍有 " + failures + " 段中文未完成";
      store.metadata("state", failures == 0 && captureError.isEmpty() ? "complete" : "incomplete");
    } catch (Throwable error) {
      status = "字幕处理停止，录音仍可继续。" + error.getClass().getSimpleName() + " " + error.getMessage();
      try {
        store.metadata("inference_error", status);
        store.metadata("state", "incomplete");
      } catch (Exception ignored) {
      }
      while (!captureDone) {
        try {
          Thread.sleep(200);
        } catch (InterruptedException ignored) {
        }
      }
    } finally {
      while (!captureDone) {
        try {
          Thread.sleep(50);
        } catch (InterruptedException ignored) {
        }
      }
      finish();
    }
  }

  private void captureEvent(FileOutputStream stream, String action, long sample)
      throws IOException {
    String event =
        "{\"kind\":\""
            + action
            + "\",\"sample\":"
            + sample
            + ",\"wall_ms\":"
            + System.currentTimeMillis()
            + "}\n";
    stream.write(event.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    stream.getFD().sync();
  }

  private void finish() {
    new Handler(Looper.getMainLooper()).post(this::finishOnMainThread);
  }

  private void finishOnMainThread() {
    if (wake != null && wake.isHeld()) wake.release();
    if (store != null) {
      try {
        store.metadata("finished_ms", "" + System.currentTimeMillis());
        store.close();
      } catch (Exception ignored) {
      }
    }
    stopForeground(STOP_FOREGROUND_REMOVE);
    stopSelf();
    active = null;
    working = false;
  }

  public void onDestroy() {
    recording = false;
    super.onDestroy();
  }
}
