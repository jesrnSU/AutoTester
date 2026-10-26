import cv2
import numpy as np
import imutils


def multi_scale_match(
    main_path, template_path, min_scale=0.2, max_scale=1.0, steps=20, threshold=0.80
):
    image_bgr = cv2.imread(main_path)
    if image_bgr is None:
        raise FileNotFoundError(f"Could not read main image: {main_path}")

    template_gray = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template_gray is None:
        raise FileNotFoundError(f"Could not read template image: {template_path}")

    image_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    tH, tW = template_gray.shape[:2]
    found = None  # will store: (maxVal, maxLoc, r)

    # Loop from large -> small
    for scale in np.linspace(max_scale, min_scale, steps):
        resized = imutils.resize(image_gray, width=int(image_gray.shape[1] * scale))
        r = image_gray.shape[1] / float(
            resized.shape[1]
        )  # ratio original_width / resized_width

        # If resized image is smaller than template, stop
        if resized.shape[0] < tH or resized.shape[1] < tW:
            break

        result = cv2.matchTemplate(resized, template_gray, cv2.TM_CCOEFF_NORMED)
        _, maxVal, _, maxLoc = cv2.minMaxLoc(result)

        if found is None or maxVal > found[0]:
            found = (maxVal, maxLoc, r)

    if found is None:
        return None

    maxVal, maxLoc, r = found

    # Convert coords back to original image size
    startX = int(maxLoc[0] * r)
    startY = int(maxLoc[1] * r)
    endX = int((maxLoc[0] + tW) * r)
    endY = int((maxLoc[1] + tH) * r)

    ok = maxVal >= threshold

    return {
        "ok": ok,
        "score": float(maxVal),
        "bbox": (startX, startY, endX, endY),
        "scale_ratio": float(r),
    }


if __name__ == "__main__":
    result = multi_scale_match(
        main_path="artifacts/example_screen.png",
        template_path="artifacts/example_screen.png",
        min_scale=0.2,
        max_scale=1.0,
        steps=25,
        threshold=0.85,
    )

    print(result)

    # Visualize match
    if result is not None:
        img = cv2.imread(
            "artifacts/example_screen.png"
        )
        x1, y1, x2, y2 = result["bbox"]
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.imshow(f"Match (score={result['score']:.3f})", img)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
