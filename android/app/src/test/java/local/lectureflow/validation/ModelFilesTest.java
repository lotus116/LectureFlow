package local.lectureflow.validation;

import static org.junit.Assert.*;

import java.io.*;
import java.nio.file.Files;
import org.junit.Test;

public class ModelFilesTest {
  @Test
  public void completedPartialCanFinishWithoutNetworkAfterInterruption() throws Exception {
    File root = Files.createTempDirectory("model-resume").toFile();
    File partial = new File(root, "model.partial");
    try {
      try (FileOutputStream out = new FileOutputStream(partial)) {
        out.write("abc".getBytes());
      }
      ModelFiles.cancel = false;
      ModelFiles.fetch(
          root,
          new String[] {
            "model",
            "invalid-network-url",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
          });
      assertTrue(new File(root, "model").isFile());
      assertFalse(partial.exists());
      assertEquals("abc", new String(Files.readAllBytes(new File(root, "model").toPath())));
    } finally {
      for (File file : root.listFiles()) file.delete();
      root.delete();
    }
  }
}
