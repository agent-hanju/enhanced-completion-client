package me.hanju.enhancedcompletion.assembler;

import java.lang.reflect.Field;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.lang.reflect.TypeVariable;
import java.util.List;

import org.junit.jupiter.api.Test;

import me.hanju.enhancedcompletion.payload.completion.BaseCompletionResponse;
import me.hanju.enhancedcompletion.payload.completion.EnhancedCompletionResponse;
import me.hanju.enhancedcompletion.payload.message.CitedMessage;

class GenericTypeTest {

  @Test
  void inspectGenericTypes() throws Exception {
    // EnhancedCompletionResponse의 부모 타입 정보
    Type superclass = EnhancedCompletionResponse.class.getGenericSuperclass();
    System.out.println("Superclass: " + superclass);
    
    if (superclass instanceof ParameterizedType pt) {
      System.out.println("  Raw type: " + pt.getRawType());
      Type[] args = pt.getActualTypeArguments();
      for (int i = 0; i < args.length; i++) {
        System.out.println("  Type arg " + i + ": " + args[i]);
      }
    }

    // choices 필드의 제네릭 타입 정보
    Field choicesField = BaseCompletionResponse.class.getDeclaredField("choices");
    Type genericType = choicesField.getGenericType();
    System.out.println("\nchoices field generic type: " + genericType);
    
    if (genericType instanceof ParameterizedType pt) {
      Type[] args = pt.getActualTypeArguments();
      System.out.println("  Type arg 0: " + args[0]);
      
      if (args[0] instanceof ParameterizedType nested) {
        System.out.println("    Nested raw: " + nested.getRawType());
        Type[] nestedArgs = nested.getActualTypeArguments();
        for (int i = 0; i < nestedArgs.length; i++) {
          System.out.println("    Nested arg " + i + ": " + nestedArgs[i] + " (" + nestedArgs[i].getClass().getSimpleName() + ")");
        }
      } else if (args[0] instanceof TypeVariable tv) {
        System.out.println("    TypeVariable name: " + tv.getName());
        System.out.println("    TypeVariable bounds: " + java.util.Arrays.toString(tv.getBounds()));
      }
    }
  }
}
