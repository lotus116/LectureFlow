package local.lectureflow.validation;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.io.*;
import java.util.*;
import org.json.JSONObject;

final class LectureStore implements AutoCloseable {
  final File folder;
  private final SQLiteDatabase db;

  LectureStore(File folder) throws Exception {
    this.folder = folder;
    folder.mkdirs();
    db = SQLiteDatabase.openOrCreateDatabase(new File(folder, "lecture.sqlite"), null);
    db.disableWriteAheadLogging();
    db.execSQL(
        "CREATE TABLE IF NOT EXISTS captions (start INTEGER PRIMARY KEY, end INTEGER NOT NULL,"
            + " english TEXT NOT NULL DEFAULT '', chinese TEXT NOT NULL DEFAULT '', final INTEGER"
            + " NOT NULL DEFAULT 0, revisions INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL"
            + " DEFAULT '')");
    db.execSQL("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)");
  }

  synchronized void metadata(String key, String value) {
    ContentValues data = new ContentValues();
    data.put("key", key);
    data.put("value", value);
    db.insertWithOnConflict("metadata", null, data, SQLiteDatabase.CONFLICT_REPLACE);
  }

  synchronized String readMetadata(String key, String fallback) {
    try (Cursor c = db.rawQuery("SELECT value FROM metadata WHERE key=?", new String[] {key})) {
      return c.moveToFirst() ? c.getString(0) : fallback;
    }
  }

  synchronized void english(long start, long end, String english, boolean stable) throws Exception {
    db.execSQL("INSERT OR IGNORE INTO captions(start,end) VALUES(?,?)", new Object[] {start, end});
    ContentValues values = new ContentValues();
    values.put("end", end);
    values.put("english", english);
    values.put("final", stable ? 1 : 0);
    db.update("captions", values, "start=?", new String[] {"" + start});
    event("english", start, english, stable ? "final" : "partial");
  }

  synchronized void translated(long start, String chinese, boolean revision) throws Exception {
    db.execSQL(
        "UPDATE captions SET chinese=?,error='',revisions=revisions+? WHERE start=?",
        new Object[] {chinese, revision ? 1 : 0, start});
    event(revision ? "revision" : "translation", start, chinese, "success");
  }

  synchronized void failed(long start, String message) throws Exception {
    db.execSQL("UPDATE captions SET error=? WHERE start=?", new Object[] {message, start});
    event("translation_failure", start, "", message);
  }

  synchronized void event(String kind, long start, String text, String state) throws Exception {
    JSONObject json = new JSONObject();
    json.put("kind", kind);
    json.put("start_sample", start);
    json.put("text", text);
    json.put("state", state);
    json.put("wall_ms", System.currentTimeMillis());
    try (FileOutputStream stream = new FileOutputStream(new File(folder, "events.jsonl"), true)) {
      stream.write((json + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
      stream.getFD().sync();
    }
  }

  synchronized List<Caption> recent(int limit) {
    ArrayList<Caption> rows = new ArrayList<>();
    try (Cursor c =
        db.rawQuery(
            "SELECT start,end,english,chinese,final,revisions,error FROM captions ORDER BY start"
                + " DESC LIMIT ?",
            new String[] {"" + limit})) {
      while (c.moveToNext())
        rows.add(
            new Caption(
                c.getLong(0),
                c.getLong(1),
                c.getString(2),
                c.getString(3),
                c.getInt(4) != 0,
                c.getInt(5),
                c.getString(6)));
    }
    Collections.reverse(rows);
    return rows;
  }

  synchronized long resumeSample() {
    try (Cursor c = db.rawQuery("SELECT COALESCE(MAX(end),0) FROM captions WHERE final=1", null)) {
      c.moveToFirst();
      return Math.max(c.getLong(0), Long.parseLong(readMetadata("processed_sample", "0")));
    }
  }

  synchronized int failures() {
    try (Cursor c = db.rawQuery("SELECT COUNT(*) FROM captions WHERE error!=''", null)) {
      c.moveToFirst();
      return c.getInt(0);
    }
  }

  synchronized void exportText(OutputStream output) throws Exception {
    try (Cursor c =
        db.rawQuery(
            "SELECT start,english,chinese,revisions,error FROM captions ORDER BY start", null)) {
      while (c.moveToNext()) {
        long seconds = c.getLong(0) / 16000;
        String row =
            String.format(java.util.Locale.ROOT, "%02d:%02d", seconds / 60, seconds % 60)
                + " · 回改 "
                + c.getInt(3)
                + " 次\n"
                + c.getString(1)
                + "\n"
                + c.getString(2)
                + "\n";
        if (!c.getString(4).isEmpty()) row += "未完成 · " + c.getString(4) + "\n";
        output.write((row + "\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
      }
    }
  }

  public synchronized void close() {
    db.close();
  }

  static final class Caption {
    final long start, end;
    final String english, chinese, error;
    final boolean stable;
    final int revisions;

    Caption(
        long start, long end, String en, String zh, boolean stable, int revisions, String error) {
      this.start = start;
      this.end = end;
      english = en;
      chinese = zh;
      this.stable = stable;
      this.revisions = revisions;
      this.error = error;
    }
  }
}
