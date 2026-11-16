package local.lectureflow.validation;

import static org.junit.Assert.*;

import androidx.test.platform.app.InstrumentationRegistry;
import java.io.*;
import org.json.JSONObject;
import org.junit.Test;

public class NativeSmokeTest {
  @Test
  public void testOfflineModels() throws Exception {
    android.content.Context context =
        InstrumentationRegistry.getInstrumentation().getTargetContext();
    File source = new File(context.getNoBackupFilesDir(), "sample.wav");
    float[] pcm;
    try (RandomAccessFile input = new RandomAccessFile(source, "r")) {
      input.seek(44);
      pcm = new float[(int) (input.length() - 44) / 2];
      for (int i = 0; i < pcm.length; i++)
        pcm[i] = (short) (input.readUnsignedByte() | (input.readUnsignedByte() << 8)) / 32768f;
    }
    JSONObject result = new JSONObject();
    long started = System.currentTimeMillis();
    try (LocalModels models = new LocalModels(ModelFiles.root(context))) {
      String english = models.recognize(pcm);
      assertTrue("ASR should contain speech", english.length() > 10);
      String initial = models.translate("The training set contains forty examples.", "", "");
      String revised =
          models.translate(
              "The training set contains forty examples.",
              "Today we discuss machine learning.",
              "I said forty, not fourteen. We use the remaining ten examples for testing.");
      assertTrue("Initial Chinese", initial.matches("(?s).*[一-鿿].*"));
      assertTrue("Revised Chinese", revised.matches("(?s).*[一-鿿].*"));
      result.put("english", english);
      result.put("initial", initial);
      result.put("revision", revised);
      result.put("elapsed_ms", System.currentTimeMillis() - started);
    }
    try (FileOutputStream output =
        new FileOutputStream(new File(context.getNoBackupFilesDir(), "native-smoke.json"))) {
      output.write(result.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }
  }
}
