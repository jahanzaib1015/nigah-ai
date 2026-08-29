import os
import sys

from dotenv import load_dotenv

IMAGE_PROMPT = (
    "What is shown in this image? "
    "If it is a currency note, tell me the denomination."
)


def main():
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is missing or empty in the .env file.")
        sys.exit(1)

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-3.6-flash")

        print("Test 1: Text prompt")
        response = model.generate_content("Hello, are you working?")
        print(response.text)

        print("\nTest 2: Image understanding")
        image_path = os.path.join(os.path.dirname(__file__) or ".", "test_image.jpg")
        if not os.path.exists(image_path):
            print(f"Error: image file not found: {image_path}")
            sys.exit(1)
        with open(image_path, "rb") as image_file:
            image_part = genai.protos.Part(
                inline_data={"mime_type": "image/jpeg", "data": image_file.read()}
            )
        response = model.generate_content([IMAGE_PROMPT, image_part])
        print(response.text)
    except KeyboardInterrupt:
        print("Request cancelled by user.")
        sys.exit(1)
    except Exception as error:
        print(f"Error: Gemini API request failed: {error}")
        print("Check that your GEMINI_API_KEY is valid and has network access.")
        sys.exit(1)


if __name__ == "__main__":
    main()
