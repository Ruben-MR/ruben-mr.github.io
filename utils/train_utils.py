import os.path
import numpy as np
import torch
import matplotlib.pyplot as plt
import torch.nn.functional as F
from utils.inference_utils import CropParameters
from tqdm import tqdm
from config import SAVED_DIR, LOG_DIR
from datetime import datetime


class PreProcessOptions:
    """
    Event preprocessing options class
    """
    def __init__(self):
        self.no_normalize = False
        self.hot_pixels_file = None
        self.flip = False


class UMSOptions:
    """
    Event preprocessing options class
    """
    def __init__(self):
        self.unsharp_mask_amount = 0.3
        self.unsharp_mask_sigma = 0.1


class RescalerOptions:
    """
    Intensity rescaler options class
    """
    def __init__(self):
        self.auto_hdr = True
        self.auto_hdr_median_filter_size = 10
        self.Imin = None
        self.Imax = None


def plot_training_data(tr_losses, v_losses):
    """
    Function for plotting the training and validation loss evolution over the iterations
    :param tr_losses: list of losses over the iterations
    :param v_losses: list of losses on the validation set
    :return: nothing
    """
    plt.style.use('seaborn')
    plt.figure(figsize=(10, 10))
    plt.subplot(2, 1, 1)
    plt.title('training loss')
    plt.xlabel('Iterations')
    plt.ylabel('Loss')
    plt.plot(tr_losses)
    plt.grid()

    plt.subplot(2, 1, 2)
    plt.title('validation loss')
    plt.xlabel('Iterations')
    plt.ylabel('Loss')
    plt.plot(v_losses)
    plt.grid()

    plt.show()


def pad_events(events, crop):
    origin_shape_events = events.shape
    events = events.unsqueeze(dim=2)
    events_after_padding = []
    for t in range(events.shape[0]):
        for item in range(events.shape[1]):
            event = events[t, item]
            event = crop.pad(event)
            events_after_padding.append(event)
    events = torch.stack(events_after_padding, dim=0)
    events = events.view(origin_shape_events[0], origin_shape_events[1], events.shape[1], events.shape[2], events.shape[3], events.shape[4]).squeeze(dim=2)
    return events

 
def pad_all(events, images, flows):
    width = events.shape[-1]
    height = events.shape[-2]
    origin_shape_events = events.shape
    origin_shape_images = images.shape
    origin_shape_flows = flows.shape
    # # ==========================
    # pre-processing step here (normalizing and padding)
    crop = CropParameters(width, height, 3)
    events = events.unsqueeze(dim=2)
    images = images.unsqueeze(dim=2)
    flows = flows.unsqueeze(dim=2)
    #images = images.unsqueeze(dim=2)
    events_after_padding = []
    images_after_padding = []
    flows_after_padding = []
    for t in range(events.shape[0]):
        for item in range(events.shape[1]):
            event = events[t, item]
            events_after_padding.append(crop.pad(event))
    for t in range(images.shape[0]):
        for item in range(images.shape[1]):
            image = images[t, item]
            images_after_padding.append(crop.pad(image))
    for t in range(flows.shape[0]):
        for item in range(flows.shape[1]):
            flow = flows[t, item]
            flows_after_padding.append(crop.pad(flow))
    events = torch.stack(events_after_padding, dim=0)
    images = torch.stack(images_after_padding, dim=0)
    flows = torch.stack(flows_after_padding, dim=0)
    events = events.view(origin_shape_events[0], origin_shape_events[1], events.shape[1], events.shape[2], events.shape[3], events.shape[4]).squeeze(dim=2)
    images = images.view(origin_shape_images[0], origin_shape_images[1], images.shape[1], images.shape[2], images.shape[3], images.shape[4]).squeeze(dim=2)
    flows = flows.view(origin_shape_flows[0], origin_shape_flows[1], flows.shape[1], flows.shape[2], flows.shape[3], flows.shape[4]).squeeze(dim=2)

    return events, images, flows


def flow_map(im, flo):
    """
    Flow mapping function, it wraps the previous image into the following timestep using the flowmap provided. The
    output will be the reconstructed image using the flow.
    :param im: tensor of shape (B, 1, H, W) containing the reconstructed images of the different batches at the previous
    time step
    :param flo: tensor of shape (B, 2, H, W) containing the flowmaps between the previous and the current/next timestep
    :return:
    """
    B, C, H, W = im.shape

    assert (im.is_cuda is True and flo.is_cuda is True) or (im.is_cuda is False and flo.is_cuda is False), \
        "both tensors should be on the same device"
    assert C == 1, "the image tensor has more than one channel"
    assert flo.shape[1] == 2, "flow tensor has wrong dimensions"

    # Create a meshgrid with pixel locations
    xx = torch.arange(0, W).view(1, -1).repeat(1, 1, H, 1)
    yy = torch.arange(0, H).view(-1, 1).repeat(1, 1, 1, W)
    xx = xx.repeat(B, 1, 1, 1)
    yy = yy.repeat(B, 1, 1, 1)
    grid = torch.cat((xx, yy), 1)

    # Move the tensor to cuda if flow and images so are
    if im.is_cuda:
        grid = grid.cuda()
    # Change the positions of the pixels indexed in the grid tensor by using the flow
    vgrid = torch.autograd.Variable(grid) + flo

    # Normalize to range [-1, 1] for usage of grid_sample
    vgrid[:, 0, :, :] = 2.0 * vgrid[:, 0, :, :].clone() / max(W - 1, 1) - 1.0
    vgrid[:, 1, :, :] = 2.0 * vgrid[:, 1, :, :].clone() / max(H - 1, 1) - 1.0

    # Permute to get the correct dimensions for the sampling function
    vgrid = vgrid.permute(0, 2, 3, 1)
    # Sample points from the previuos image according to the indexing grid

    output = F.grid_sample(im, vgrid)

    """
    mask = torch.autograd.Variable(torch.ones(im.size())).cuda()
    mask = F.grid_sample(mask.double(), vgrid)
    mask[mask < 0.9999] = 0
    mask[mask > 0] = 1
    output *= mask
    """
    return output


def loss_fn(I_pred, I_pred_pre, I_true, I_true_pre, reconstruction_loss_fn, flow=None, first_iteration=False):
    """
    Custom loss function as specified by the authors, takes the current and last predicted and ground-truth images
    and computes the loss function with perceptual and temporal consistency components using a value of 50 for the alpha
    weighing constant of the temporal consistency loss and lambda value of 5 for weighing both components of the loss
    :param I_pred: latest predicted image
    :param I_pred_pre: predicted image at the previous timestep
    :param I_true: ground-truth image of the latest prediction
    :param I_true_pre: ground-truth image of the previous timestep
    :param flow: flow tensor between the previous and the current timestep of the sequence
    :param first_iteration: boolean for skipping the temporal consistency loss if the loss is being computed for the
    first timestep of the sequence
    :return: value of the loss function
    """
    # reconstruction loss
    # image should be RGB, IMPORTANT: normalized to [-1,1]
    reconstruction_loss = reconstruction_loss_fn(I_pred, I_true)

    # temporal consistency loss
    if not first_iteration:
        alpha = 50  # hyper-parameter for weighting term (mitigate the effect of occlusions)
        # TODO: verify correct working
        if flow is not None:
            # Computation of the flow map operation upon the predicted images
            W = flow_map(I_pred_pre, flow)
            # Computation of the weighing term using the previous and current ground-truth images
            M = torch.exp(-alpha * torch.linalg.norm(I_true - flow_map(I_true_pre, flow), ord=2, dim=(-1, -2)))
        else:
            W, M = 1, 1
        # Compute the temporal loss
        temporal_loss = M * torch.linalg.norm(I_pred - W, ord=1, dim=(-1, -2))
    else:
        temporal_loss = 0

    # total loss
    lambda_ = 5  # weighting hyper-parameter
    loss = reconstruction_loss + lambda_ * temporal_loss

    return loss


# Training function
def training_loop(model, train_loader, validation_loader, rec_fun, cropper, preproc, postproc, filt=None, lr=1e-4, epoch=5, save=True):
    """
    Function for implementing the training loop of the network
    :param model: network to be trained
    :param train_loader: data loader
    :param validation_loader: validation data loader
    :param rec_fun: reconstruction loss function
    :param cropper: class for cropping the event data before feeding the network
    :param preproc: class for performing event tensor preprocessing (normalization for now)
    :param postproc: class for performing event tensor postprocessing (intensity rescaling)
    :param lr:learning rate
    :param epoch:number of epochs of the training
    :return: list of training and validation losses
    """
    print(lr)
    time_before_train = datetime.now()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    train_losses = []   # mean loss over each epoch
    val_losses = []  # mean loss over each epoch
    # Start iterating for the specific number of epochs
    for e in range(epoch):
        print("\ntranining progress: epoch {}".format(e + 1))
        epoch_losses = []  # loss of each batch
        # Load the current data batch
        for x_batch, y_batch, flow_batch in tqdm(train_loader):
            hidden_states = None
            I_predict_previous = None
            x_batch = preproc(x_batch)
            x_batch = pad_events(x_batch, cropper)
            # Iterate over the timesteps (??)
            for t in range(x_batch.shape[1]):
                # TODO: discuss these changes
                # Modify accordingly in the validation part
                # Option 1: not perform temporal consistency loss on first iteration (actually first two, according to
                # authors) However, we will probably end up with much smaller sequences, so I am not sure whether we can
                # afford to do this
                if t < 1:
                    I_predict, hidden_states = model(x_batch[:, t], None)
                    I_predict = I_predict[:, :, cropper.iy0:cropper.iy1, cropper.ix0:cropper.ix1]
                    if filt is not None:
                        I_predict = filt(I_predict)
                    I_predict = postproc(I_predict)
                    # print(x_batch[t].shape, I_predict.shape, y_batch[t].shape)
                    loss = loss_fn(I_predict, None, y_batch[:, t + 1], None, rec_fun,
                                   flow=None, first_iteration=True).sum()
                else:
                    I_predict, hidden_states = model(x_batch[:, t], hidden_states)
                    I_predict = I_predict[:, :, cropper.iy0:cropper.iy1, cropper.ix0:cropper.ix1]
                    if filt is not None:
                        I_predict = filt(I_predict)
                    I_predict = postproc(I_predict)
                    loss += loss_fn(I_predict, I_predict_previous, y_batch[:, t + 1], y_batch[:, t], rec_fun,
                                    flow=flow_batch[:, t]).sum()
                # update variables
                I_predict_previous = I_predict
            # model update
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            epoch_losses.append(loss.item())  # loss for single epoch
        train_losses.append(np.sum(epoch_losses))

        # After every epoch, perform validation
        with torch.no_grad():
            epoch_losses = []  # loss of each batch
            print("\nvalidation progress:")
            # Load the data

            for x_batch_val, y_batch_val, flow_batch_val in tqdm(validation_loader):
                hidden_states = None
                I_predict_previous = None
                x_batch_val = preproc(x_batch_val)
                x_batch_val = pad_events(x_batch_val, cropper)
                batch_loss = 0
                for t in range(x_batch_val.shape[1]):
                    if t < 1:
                        I_predict, hidden_states = model(x_batch_val[:, t], None)
                        I_predict = I_predict[:, :, cropper.iy0:cropper.iy1, cropper.ix0:cropper.ix1]
                        if filt is not None:
                            I_predict = filt(I_predict)
                        I_predict = postproc(I_predict)
                        loss = loss_fn(I_predict, None, y_batch_val[:, t + 1], None, rec_fun,
                                       flow=None, first_iteration=True).sum()
                    else:
                        I_predict, hidden_states = model(x_batch_val[:, t], hidden_states)
                        I_predict = I_predict[:, :, cropper.iy0:cropper.iy1, cropper.ix0:cropper.ix1]
                        if filt is not None:
                            I_predict = filt(I_predict)
                        I_predict = postproc(I_predict)
                        loss = loss_fn(I_predict, I_predict_previous, y_batch_val[:, t + 1], y_batch_val[:, t], rec_fun,
                                       flow=flow_batch_val[:, t]).sum()
                    # update variables
                    I_predict_previous = I_predict
                    batch_loss += loss.item()
                epoch_losses.append(batch_loss)  # loss for single epoch
            val_losses.append(np.sum(epoch_losses))
    time_after_train = datetime.now()
    training_time = (time_after_train - time_before_train).seconds / 60 # in minutes
    print('total_training_time:{} minutes'.format(training_time))

    if save:
        name = datetime.now().strftime("saved_%d-%m-%Y_%H-%M")
        fullpath = os.path.join(SAVED_DIR, name)
        torch.save(model.state_dict(), fullpath)
        print(f"SAVED MODEL AS:\n"
              f"{name}\n"
              f"in: {SAVED_DIR}")

    data = np.array([train_losses, val_losses]).T
    filename = datetime.now().strftime("saved_%d-%m-%Y_%H-%M.csv")
    fullpath = os.path.join(LOG_DIR, filename)
    np.savetxt(fullpath, data, delimiter=',')

    return train_losses, val_losses
