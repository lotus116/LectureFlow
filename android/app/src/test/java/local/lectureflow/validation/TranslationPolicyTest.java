package local.lectureflow.validation;

import org.junit.Test;
import static org.junit.Assert.*;

public class TranslationPolicyTest {
    @Test public void writtenEnglishNumbersCanBecomeDigits() {
        assertEquals("包含40个示例。", TranslationPolicy.validate("There are forty examples.", "包含40个示例。"));
    }
    @Test public void referenceObjectLeakWithWhitespaceIsRejected() {
        assertThrows(IllegalStateException.class, () -> TranslationPolicy.validate("Hello.", "{\"following\" : \"你好\"}"));
    }
    @Test public void sourceActuallyDiscussingReferenceFieldIsAllowed() {
        String literal = "\"following\" : value";
        assertEquals(literal, TranslationPolicy.validate(literal, literal));
    }
    @Test public void referenceSpeechIsEscapedAndTargetPlacedLast() {
        String prompt = TranslationPolicy.prompt("Translate only me.", "Earlier \"quote\"\nline", "Future sentence.");
        assertTrue(prompt.contains("Earlier \\\"quote\\\"\\nline"));
        assertTrue(prompt.endsWith("Translate only me."));
    }
    @Test public void emptyTranslationCannotOverwriteEarlierText() {
        assertThrows(IllegalStateException.class, () -> TranslationPolicy.validate("Hello.", "  \n"));
    }
}
