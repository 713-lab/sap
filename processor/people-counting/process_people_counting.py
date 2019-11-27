#########################################################
# @names process_people_counting.py
# @author Chia Hsin Hsu
#
# @params input Input Pulsar topic
# @params output Output Pulsar topic
# @params url Pulsar service url
# @params darknet_dir the dir of darknet folder
# @params debug debug mode when debug=1, default=0
#
#########################################################
from PeopleCountingDetector import PeopleCountingDetector
import argparse
import cv2
import numpy as np
import pulsar
from pulsar import MessageId
from pulsar.schema import *
import time
import mxnet as mx
import ast
import configparser

class Frame(Record):
    timestamp = String()
    img = Bytes()

class TextFrame(Record):
    timestamp = String()
    people = String()

class YOLOFrame(Record):
    timestamp = String()
    processed_img = Bytes()
    detections = String()
  
ap = argparse.ArgumentParser()
ap.add_argument("-c", "--camera", required=True,
    help="select camera setting")

args = vars(ap.parse_args())

config = configparser.ConfigParser()
config.read(args['camera']+'.ini')

processor = config['processor']

############################################################
# 1. Connect to Pulsar and create 
# 2. Initial YoloDetector to use Yolov3
############################################################
debug = processor['debug']
client = pulsar.Client(processor['pulsar_url'])
if(debug):	print("[Info] Create Client to " + processor['pulsar_url'])

sidewalk_poly = [ [float(processor['upper_left_x']) , float(processor['upper_left_y'])], [float(processor['lower_left_x']) , float(processor['lower_left_y'])], [float(processor['lower_right_x']) , float(processor['lower_right_y'])], [float(processor['upper_right_x']) , float(processor['upper_right_y'])]  ]
print("sidewalk_poly:")
print(sidewalk_poly)
sidewalk_poly  = np.array([sidewalk_poly ], dtype = np.int32)

reader = client.create_reader(
                                topic=processor['input_topic'],  
                                start_message_id=MessageId.latest, 
                                receiver_queue_size=5000,
                                schema=AvroSchema(YOLOFrame)
                                )

producer = client.create_producer(
                topic=processor['output_topic'],
                schema=AvroSchema(YOLOFrame))

producer2 = client.create_producer(
                topic=processor['output_topic2'],
                schema=AvroSchema(TextFrame))

count_people = 0
count = 0
amount_of_people = 0

print("[Info] start sending data")

while True:
    try:
        #############################################################
        # Get Input Streaming data
        # 1. Read data from Pulsar
        # 2. Decode img from bytes to numpy.ndarray
        #############################################################
        prev = time.time()
        msg = reader.read_next()
        #print(msg.publish_timestamp())
        time1 = time.time()
        if(debug): print("[Time] read from pulsar {}".format(1/(time1-prev)))
        time2 = time.time()
        #frame = cv2.imdecode(np.frombuffer(msg.value().img, np.uint8), -1)
        # Alternative decoding with mxnet.image
        detections = ast.literal_eval(msg.value().detections)
        img_ndarray = mx.image.imdecode(msg.value().processed_img)
        frame = img_ndarray.asnumpy()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        time3 = time.time()
        if(debug): print("[Time] imdecode from bytes to numpy.ndarray{}".format(1/(time3-time2)))
        #############################################################
        # Process image by yolo 
        # 1. Get the detections which generated by Yolov3
        # 2. Get the resized frame (416*416)
        #############################################################
        (H, W) = frame.shape[:2]
        H = 864/416*1.8
        W = 1152/416*1.8


       

        time2 = time.time()
        
        #sidewalk_poly = [[44.6854219949, 172.358056266], [0, 397.9130434783], [135.1202046036, 392.5933503836], [120.2250639386, 155.3350383632]]
        #sidewalk_poly = np.array([sidewalk_poly], dtype = np.int32)
        cv2.polylines(frame, sidewalk_poly, True, (255,0,0),2)
        length_of_detections = len(detections)
        for detection in detections:
            object_name = detection[0].decode("utf-8") 
            if(debug): print(object_name)
            if(object_name != 'person'):  continue
            x = detection[2]
            y = detection[3]
            w = detection[4]
            h = detection[5]
            xmin = int(round(x - (w / 2)))
            xmax = int(round(x + (w / 2)))
            ymin = int(round(y - (h / 2)))
            ymax = int(round(y + (h / 2)))
            #print(xmin, xmax, ymin, ymax)
            pt1 = (xmin, ymin)
            pt2 = (xmax, ymax)
            cv2.rectangle(frame, pt1, pt2, (0, 0, 255), 4)
            cv2.putText(frame,
                        detection[0].decode() +
                        " [" + str(round(detection[1] * 100, 2)) + "]",
                        (pt1[0], pt1[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        [0, 0, 255], 2)

            count_people = count_people + PeopleCountingDetector.detect(sidewalk_poly, (xmin+xmax)/2, (ymax+ymax)/2)

        time3 = time.time()
        if(debug): print("[Time] detect {} in {} FPS".format(length_of_detections,1/(time3-time2)))

        count = count + 1
        if(count >= 5):
            amount_of_people = round(count_people/count)
            count = 0
            count_people = 0
        
        text = "# of People: {}".format(amount_of_people)
        cv2.putText(frame, text, (220, 400), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 2)

        #########################################################################
        # Produce processed message to Pulsar
        # 1. Encode frame(numpy.ndarray) to jpg 
        # 2. Initialize message
        # 3. Produce it to Pular
        #########################################################################

        ret, jpeg = cv2.imencode('.jpg', frame)
        _t = msg.value().timestamp
        _Y = YOLOFrame(timestamp=_t,
                        processed_img=jpeg.tobytes(),
                        detections=str(amount_of_people))

        _Text = TextFrame(timestamp=_t,
                            people=str(amount_of_people))
        
        producer.send(_Y)
        producer2.send(_Text)
        if(debug): print("send data")
        #if(debug): print(isIllegalPark, coverRatio)
        time2 = time.time()
        if(debug): print("[Time] send data to pulsar {}".format(1/(time2-time3)))
        cv2.waitKey(1)
        print(f'Fps {time.asctime( time.localtime(time.time()) )} {1/(time.time() - prev)}')
        print("Amount of people: ", amount_of_people)
    except Exception as e:
        print(e)
    


