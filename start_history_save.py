import matplotlib.pyplot as plt
from IPython.display import clear_output
import tensorflow as tf
import numpy as np

from tensorflow_examples.models.pix2pix import pix2pix

import tensorflow_datasets as tfds

tfds.disable_progress_bar()


data_dir = "./data"
dataset, info = tfds.load("oxford_iiit_pet:3.*.*",
                          with_info=True, data_dir=data_dir)


def normalize(input_image, input_mask):
    input_image = tf.cast(input_image, tf.float32) / 255.0
    input_mask -= 1
    return input_image, input_mask


@tf.function
def load_image_train(datapoint):
    input_image = tf.image.resize(datapoint["image"], (128, 128))
    input_mask = tf.image.resize(datapoint["segmentation_mask"], (128, 128))

    if tf.random.uniform(()) > 0.5:
        input_image = tf.image.flip_left_right(input_image)
        input_mask = tf.image.flip_left_right(input_mask)

    input_image, input_mask = normalize(input_image, input_mask)

    return input_image, input_mask


def load_image_test(datapoint):
    input_image = tf.image.resize(datapoint["image"], (128, 128))
    input_mask = tf.image.resize(datapoint["segmentation_mask"], (128, 128))

    input_image, input_mask = normalize(input_image, input_mask)

    return input_image, input_mask


TRAIN_LENGTH = info.splits["train"].num_examples
BATCH_SIZE = 64
BUFFER_SIZE = 1000
STEPS_PER_EPOCH = TRAIN_LENGTH // BATCH_SIZE

# 머신 러닝의 궁극적인 목표는 training dataset을 이용하여 학습한 모델을 가지고 test dataset를 예측하는 것이다.
# 이 때 test dataset은 학습 과정에서 참조할 수 없다고 가정하기 때문에
# 머신 러닝 모델은 training dataset만을 가지고 test dataset을 잘 예측하도록 학습되어야 한다.

train = dataset["train"].map(
    load_image_train, num_parallel_calls=tf.data.experimental.AUTOTUNE
)
test = dataset["test"].map(load_image_test)

# 주어진 dataset을 training, validation, test dataset들로 나눈다.
# 일반적으로 각 dataset의 비율은 60:20:20으로 설정

train_dataset = train.cache().shuffle(BUFFER_SIZE).batch(BATCH_SIZE).repeat()
train_dataset = train_dataset.prefetch(
    buffer_size=tf.data.experimental.AUTOTUNE)
test_dataset = test.batch(BATCH_SIZE)


def display(display_list):
    plt.figure(figsize=(15, 15))

    title = ["Input Image", "True Mask", "Predicted Mask"]

    for i in range(len(display_list)):
        plt.subplot(1, len(display_list), i + 1)
        plt.title(title[i])
        plt.imshow(tf.keras.preprocessing.image.array_to_img(display_list[i]))
        plt.axis("off")
    plt.show()


for image, mask in train.take(1):
    sample_image, sample_mask = image, mask
# display([sample_image, sample_mask])

OUTPUT_CHANNELS = 3

base_model = tf.keras.applications.MobileNetV2(
    input_shape=[128, 128, 3], include_top=False
)

# 이 층들의 활성화를 이용합시다
layer_names = [
    "block_1_expand_relu",  # 64x64
    "block_3_expand_relu",  # 32x32
    "block_6_expand_relu",  # 16x16
    "block_13_expand_relu",  # 8x8
    "block_16_project",  # 4x4
]
layers = [base_model.get_layer(name).output for name in layer_names]

# 특징추출 모델을 만듭시다
down_stack = tf.keras.Model(inputs=base_model.input, outputs=layers)

down_stack.trainable = False

up_stack = [
    pix2pix.upsample(512, 3),  # 4x4 -> 8x8
    pix2pix.upsample(256, 3),  # 8x8 -> 16x16
    pix2pix.upsample(128, 3),  # 16x16 -> 32x32
    pix2pix.upsample(64, 3),  # 32x32 -> 64x64
]


def unet_model(output_channels):
    inputs = tf.keras.layers.Input(shape=[128, 128, 3])
    x = inputs

    # 모델을 통해 다운샘플링합시다
    skips = down_stack(x)
    x = skips[-1]
    skips = reversed(skips[:-1])

    # 건너뛰기 연결을 업샘플링하고 설정하세요
    for up, skip in zip(up_stack, skips):
        x = up(x)
        concat = tf.keras.layers.Concatenate()
        x = concat([x, skip])

    # 이 모델의 마지막 층입니다
    last = tf.keras.layers.Conv2DTranspose(
        output_channels, 3, strides=2, padding="same"
    )  # 64x64 -> 128x128

    x = last(x)

    return tf.keras.Model(inputs=inputs, outputs=x)


model = unet_model(OUTPUT_CHANNELS)
model.compile(
    optimizer="adam",
    loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
    metrics=["accuracy"],
)

# 모델 시각화
tf.keras.utils.plot_model(model, show_shapes=True)


def create_mask(pred_mask):
    pred_mask = tf.argmax(pred_mask, axis=-1)
    pred_mask = pred_mask[..., tf.newaxis]
    return pred_mask[0]


def show_predictions(dataset=None, num=1):
    if dataset:
        for image, mask in dataset.take(num):
            pred_mask = model.predict(image)
            display([image[0], mask[0], create_mask(pred_mask)])
    else:
        display(
            [
                sample_image,
                sample_mask,
                create_mask(model.predict(sample_image[tf.newaxis, ...])),
            ]
        )


# show_predictions()

# 훈련 단계
class DisplayCallback(tf.keras.callbacks.Callback):
    def on_epoch_end(self, epoch, logs=None):
        clear_output(wait=True)
        # show_predictions()
        print("\n에포크 이후 예측 예시 {}\n".format(epoch + 1))


EPOCHS = 20
VAL_SUBSPLITS = 5
VALIDATION_STEPS = info.splits["test"].num_examples // BATCH_SIZE // VAL_SUBSPLITS

# CSVLOGGER 콜백 생성
# 훈련 기록을 저장하기 위한 설정
filename = 'log.csv'
history_logger = tf.keras.callbacks.CSVLogger(
    filename, separator=",", append=True)

# 훈련 실행
model_history = model.fit(
    train_dataset,
    epochs=EPOCHS,
    steps_per_epoch=STEPS_PER_EPOCH,
    validation_steps=VALIDATION_STEPS,
    validation_data=test_dataset,
    callbacks=[DisplayCallback(), history_logger],
)

np.save('history1.npy', model_history.history)
# training loss - 훈련 손실
# validation loss - 검증 손실
loss = model_history.history["loss"]
val_loss = model_history.history["val_loss"]


epochs = range(EPOCHS)

# 훈련 결과 차트 출력
# 차트 이해하기 https://untitledtblog.tistory.com/158
plt.figure()
plt.plot(epochs, loss, "r", label="Training loss")
plt.plot(epochs, val_loss, "bo", label="Validation loss")
plt.title("Training and Validation Loss")
plt.xlabel("Epoch")
plt.ylabel("Loss Value")
plt.ylim([0, 1])
plt.legend()
plt.show()

show_predictions(test_dataset, 3)
