package local.lectureflow.validation;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.nio.*;
import java.util.*;
import java.util.zip.*;

public final class MainActivity extends Activity {
  private final Handler handler = new Handler(Looper.getMainLooper());
  private TextView status, modelStatus, transcript;
  private Button start, pause, stop, prepare, export, history;
  private File selected;
  private LectureStore viewing;
  private volatile boolean exporting;
  private File exportFolder;
  private ScrollView scroll;
  private final Runnable refresh =
      new Runnable() {
        public void run() {
          render();
          handler.postDelayed(this, 1000);
        }
      };

  public void onCreate(Bundle state) {
    super.onCreate(state);
    getWindow().setStatusBarColor(Color.rgb(232, 245, 252));
    getWindow().setNavigationBarColor(Color.WHITE);
    LinearLayout root = new LinearLayout(this);
    root.setOrientation(LinearLayout.VERTICAL);
    root.setPadding(dp(16), dp(16), dp(16), dp(8));
    root.setBackgroundColor(Color.rgb(242, 249, 253));
    root.setOnApplyWindowInsetsListener(
        (v, insets) -> {
          v.setPadding(
              dp(16),
              insets.getSystemWindowInsetTop() + dp(12),
              dp(16),
              insets.getSystemWindowInsetBottom() + dp(8));
          return insets;
        });
    setContentView(root);
    TextView title = new TextView(this);
    title.setText("LectureFlow");
    title.setTextSize(28);
    title.setTextColor(Color.rgb(23, 57, 80));
    root.addView(title);
    modelStatus = text(root, 14);
    prepare =
        button(
            root,
            "准备本地模型",
            () -> {
              if (ModelFiles.downloading) ModelFiles.cancel = true;
              else ModelFiles.prepare(this);
            });
    status = text(root, 16);
    LinearLayout controls = new LinearLayout(this);
    root.addView(controls);
    start = button(controls, "开始 Lecture", this::startLecture);
    pause = button(controls, "暂停", () -> command("pause"));
    stop = button(root, "结束并补齐字幕", () -> command("stop"));
    LinearLayout files = new LinearLayout(this);
    root.addView(files);
    history = button(files, "历史录音", this::chooseLecture);
    export = button(files, "导出本讲 ZIP", this::exportLecture);
    scroll = new ScrollView(this);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    transcript = new TextView(this);
    transcript.setTextSize(18);
    transcript.setTextColor(Color.rgb(25, 43, 57));
    transcript.setTextIsSelectable(true);
    transcript.setLineSpacing(8, 1);
    scroll.addView(transcript);
    selectLatest();
  }

  private int dp(int value) {
    return Math.round(value * getResources().getDisplayMetrics().density);
  }

  private TextView text(LinearLayout parent, int size) {
    TextView t = new TextView(this);
    t.setTextSize(size);
    t.setTextColor(Color.rgb(32, 60, 78));
    t.setPadding(0, 10, 0, 10);
    parent.addView(t);
    return t;
  }

  private Button button(LinearLayout parent, String label, Runnable action) {
    Button b = new Button(this);
    b.setText(label);
    b.setAllCaps(false);
    b.setMinHeight(dp(52));
    b.setOnClickListener(v -> action.run());
    if (parent.getOrientation() == LinearLayout.HORIZONTAL)
      parent.addView(b, new LinearLayout.LayoutParams(0, -2, 1));
    else parent.addView(b);
    return b;
  }

  public void onResume() {
    super.onResume();
    handler.post(refresh);
  }

  public void onPause() {
    handler.removeCallbacks(refresh);
    super.onPause();
  }

  public void onDestroy() {
    if (viewing != null) viewing.close();
    super.onDestroy();
  }

  private void startLecture() {
    if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
        != PackageManager.PERMISSION_GRANTED) {
      requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, 4);
      return;
    }
    if (Build.VERSION.SDK_INT >= 33
        && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED)
      requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 5);
    if (viewing != null) {
      viewing.close();
      viewing = null;
    }
    startForegroundService(new Intent(this, CaptureService.class).setAction("start"));
  }

  public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
    super.onRequestPermissionsResult(request, permissions, results);
    if (request == 4 && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED)
      startLecture();
  }

  private void command(String action) {
    startService(new Intent(this, CaptureService.class).setAction(action));
  }

  private void render() {
    CaptureService active = CaptureService.active;
    boolean running = active != null && active.working;
    boolean ready = ModelFiles.ready(this);
    modelStatus.setText(ready ? "离线模型已准备 · 英文识别 / 中文翻译" : ModelFiles.progress);
    prepare.setVisibility(ready ? View.GONE : View.VISIBLE);
    prepare.setText(ModelFiles.downloading ? "暂停下载" : "准备 / 继续下载模型");
    prepare.setEnabled(!running);
    start.setEnabled(ready && !running && !exporting && !ModelFiles.downloading);
    pause.setEnabled(running && active.recording);
    stop.setEnabled(running && active.recording);
    pause.setText(running && active.paused ? "继续" : "暂停");
    history.setEnabled(!running && !exporting);
    export.setEnabled(!running && selected != null && !exporting);
    status.setText(
        (running ? CaptureService.status : historicalStatus())
            + (running
                ? "\n已录 "
                    + time(active.samples)
                    + " · 字幕待处理约 "
                    + Math.max(0, (active.samples - active.consumed) / 16000)
                    + " 秒"
                : ""));
    if (running && CaptureService.latest != null && !CaptureService.latest.equals(selected))
      select(CaptureService.latest);
    if (viewing != null) {
      StringBuilder value = new StringBuilder();
      for (LectureStore.Caption c : viewing.recent(40)) {
        value.append(time(c.start)).append(c.stable ? "" : " · 识别中");
        if (c.revisions > 0) value.append(" · 回改 ").append(c.revisions).append(" 次");
        value
            .append('\n')
            .append(c.english)
            .append('\n')
            .append(c.chinese.isEmpty() ? "中文待处理" : c.chinese);
        if (!c.error.isEmpty()) value.append("\n未完成 · ").append(c.error);
        value.append("\n\n");
      }
      if (value.length() == 0) value.append("开始讲话后，英文和中文会出现在这里。\n\n原始录音独立保存。结束后请等待字幕补齐，再导出本讲。");
      String current = value.toString();
      if (!current.contentEquals(transcript.getText())) {
        boolean follow = transcript.getHeight() - scroll.getHeight() - scroll.getScrollY() < dp(80);
        transcript.setText(current);
        if (follow) scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
      }
    }
  }

  private static String time(long samples) {
    long seconds = samples / 16000;
    return String.format(Locale.ROOT, "%02d:%02d", seconds / 60, seconds % 60);
  }

  private File[] lectures() {
    File[] files = new File(getNoBackupFilesDir(), "lectures").listFiles(File::isDirectory);
    if (files == null) return new File[0];
    Arrays.sort(files, Comparator.comparing(File::getName).reversed());
    return files;
  }

  private void selectLatest() {
    File[] files = lectures();
    if (files.length > 0) select(files[0]);
  }

  private void select(File folder) {
    try {
      if (viewing != null) viewing.close();
      viewing = new LectureStore(folder);
      selected = folder;
    } catch (Exception e) {
      message(e.getMessage());
    }
  }

  private void chooseLecture() {
    File[] files = lectures();
    String[] names = new String[files.length];
    for (int i = 0; i < files.length; i++) names[i] = files[i].getName();
    new AlertDialog.Builder(this)
        .setTitle("已保存的 Lecture")
        .setItems(
            names,
            (dialog, index) -> {
              select(files[index]);
              CaptureService.status = "查看 " + files[index].getName();
              render();
            })
        .setNegativeButton("关闭", null)
        .show();
  }

  private void exportLecture() {
    exportFolder = selected;
    exporting = true;
    Intent intent =
        new Intent(Intent.ACTION_CREATE_DOCUMENT)
            .setType("application/zip")
            .addCategory(Intent.CATEGORY_OPENABLE)
            .putExtra(Intent.EXTRA_TITLE, "LectureFlow-" + selected.getName() + ".zip");
    startActivityForResult(intent, 7);
  }

  protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request != 7) return;
    if (result != RESULT_OK || data == null) {
      exporting = false;
      return;
    }
    File folder = exportFolder;
    new Thread(
            () -> {
              try {
                try (OutputStream output = getContentResolver().openOutputStream(data.getData());
                    ZipOutputStream zip = new ZipOutputStream(output)) {
                  File[] files = folder.listFiles();
                  if (files == null) throw new IOException("本讲目录不可读");
                  for (File file : files) {
                    if (!file.isFile() || file.getName().endsWith("-journal")) continue;
                    zip.putNextEntry(
                        new ZipEntry(
                            file.getName().equals("recording.pcm")
                                ? "recording.wav"
                                : file.getName()));
                    if (file.getName().equals("recording.pcm")) zip.write(wavHeader(file.length()));
                    try (FileInputStream input = new FileInputStream(file)) {
                      byte[] buffer = new byte[65536];
                      int count;
                      while ((count = input.read(buffer)) >= 0) zip.write(buffer, 0, count);
                    }
                    zip.closeEntry();
                  }
                  zip.putNextEntry(new ZipEntry("transcript.txt"));
                  try (LectureStore source = new LectureStore(folder)) {
                    source.exportText(zip);
                  }
                  zip.closeEntry();
                }
                runOnUiThread(() -> message("本讲 ZIP 已导出，包含原音、字幕历史和测试记录"));
              } catch (Exception error) {
                runOnUiThread(() -> message("导出失败，" + error.getMessage()));
              } finally {
                exporting = false;
              }
            },
            "lecture-export")
        .start();
  }

  static byte[] wavHeader(long bytes) throws IOException {
    if (bytes > 0xffffffffL - 36) throw new IOException("录音超出 WAV 大小限制");
    ByteBuffer b = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
    b.put("RIFF".getBytes())
        .putInt((int) bytes + 36)
        .put("WAVEfmt ".getBytes())
        .putInt(16)
        .putShort((short) 1)
        .putShort((short) 1)
        .putInt(16000)
        .putInt(32000)
        .putShort((short) 2)
        .putShort((short) 16)
        .put("data".getBytes())
        .putInt((int) bytes);
    return b.array();
  }

  private String historicalStatus() {
    if (viewing == null) return "准备开始";
    String state = viewing.readMetadata("state", "incomplete");
    String error = viewing.readMetadata("capture_error", "");
    if (error.isEmpty()) error = viewing.readMetadata("inference_error", "");
    return selected.getName()
        + "\n"
        + (state.equals("complete") ? "已完成" : "未完成，原音与已有字幕已保存")
        + (error.isEmpty() ? "" : "\n" + error);
  }

  private void message(String value) {
    new AlertDialog.Builder(this).setMessage(value).setPositiveButton("知道了", null).show();
  }
}
